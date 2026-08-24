import logging
import os
from pathlib import Path

import torch

from src.pipelines.pipes.generator.sdxl.pipeline import StableDiffusionXLKDiffusionPipeline
from vendor.gpl.fooocus.patch import PatchSettings
from src.pipelines.outputs import ImageGenerationOutput, ProgressGenerationOutput, GenerationOutput
from src.platform.observability.logger import logger
from src.pipelines.models import Model, Text2ImageMixin, Image2ImageMixin
from src.platform.runtime.primitives.clip import ConditioningModel
from src.pipelines.contracts import IOType
from src.pipelines.contracts import GenerationInput
from src.pipelines.models import BaseModel
from src.pipelines.outputs import Progress
from src.platform.util.latents import latents_to_rgb
from src.pipelines.pipes._shared.models.sdxl.parameter_adapter import SDXLParameterAdapter
from src.platform.runtime.device import log_memory_usage
from src.pipelines.pipes.checkpoint_loader.sdxl.model_type_detector import SDXLModelTypeDetector, SDXLModelTypeInfo
from src.pipelines.pipes.checkpoint_loader.sdxl.memory_strategy import apply_to_pipeline
from src.platform.runtime.model_lifecycle.memory_policy import MemoryPolicy

# Import standard diffusers for ControlNet support
try:
    from diffusers import StableDiffusionXLControlNetPipeline, ControlNetModel
    from diffusers import MultiControlNetModel
    DIFFUSERS_CONTROLNET_AVAILABLE = True
except ImportError:
    DIFFUSERS_CONTROLNET_AVAILABLE = False
    logger.warning("[MODEL][SDXL] Standard diffusers ControlNet not available")


# Vendored diffusers-format pipeline config (model_index.json + per-component
# config.json + CLIP tokenizer vocab) for stabilityai/stable-diffusion-xl-base-1.0.
# Passed explicitly to every from_single_file() call below so config resolution
# never touches the Hugging Face Hub — the native engine defaults HF_HUB_OFFLINE=1
# (see text_encoders/tokenization.py), and without this, from_single_file() falls
# back to fetching this same config from the hub, raising LocalEntryNotFoundError
# on any machine without a warm HF cache. See assets/sdxl_base_pipeline_config/README.md.
SDXL_BASE_PIPELINE_CONFIG = str(Path(__file__).resolve().parent / "assets" / "sdxl_base_pipeline_config")


class SDXLModel(Model, Text2ImageMixin, Image2ImageMixin):
    """SDXL-specific model implementation"""

    def __init__(self, template, config):
        super().__init__(config)
        self.template = template
        self.scheduler = None
        self.controlnets = None  # Store controlnet models for pipeline initialization
        self.using_controlnet = False  # Track if we're using controlnet pipeline
        self._inference_device = None  # Track actual inference device (cuda/cpu) for Generator creation
        self.model_type_info = None  # Will be set after model load
        self.denoising_hooks = {}  # name -> DenoisingHook

    def register_hook(self, name: str, hook):
        """Register a named denoising hook. Overwrites existing hook with same name."""
        self.denoising_hooks[name] = hook
        logger.debug(f"[MODEL][SDXL] Registered denoising hook: {name}")

    def clear_hooks(self):
        """Clear all registered hooks. Called before each generation to prevent stale hooks from cached models."""
        if self.denoising_hooks:
            logger.debug(f"[MODEL][SDXL] Clearing {len(self.denoising_hooks)} hooks: {list(self.denoising_hooks.keys())}")
        self.denoising_hooks = {}

    def get_ordered_hooks(self) -> list:
        """Return hooks sorted by priority (lower first)."""
        return sorted(self.denoising_hooks.values(), key=lambda h: h.priority)

    def validate_configuration(self):
        if not self.config.get("path", None) or not os.path.exists(self.config.get("path")):
            raise ValueError("[MODEL][SDXL] Missing or invalid path")

        if self.config.get("loras", None):
            for lora in self.config.get("loras"):
                if not os.path.exists(lora[0]):
                    raise ValueError(f"[MODEL][SDXL] Missing or invalid LoRA path: {lora[0]}")

        if self.config.get("dtype", None) not in ["float16", "float32"]:
            raise ValueError(f"[MODEL][SDXL] Invalid dtype: {self.config.get('dtype')}")

        if self.config.get("device", None) not in ["cpu", "cuda"]:
            raise ValueError(f"[MODEL][SDXL] Invalid device: {self.config.get('device')}")

        if self.config.get("nsfw", None) not in [True, False]:
            raise ValueError(f"[MODEL][SDXL] Invalid nsfw: {self.config.get('nsfw')}")

    def _check_and_fix_scheduler_from_checkpoint(self, pipe):
        """Ensure scheduler uses standard SDXL noise schedule (ComfyUI-compatible).

        ComfyUI ALWAYS computes alphas_cumprod from standard SDXL betas
        (beta_start=0.00085, beta_end=0.012, scaled_linear, 1000 steps),
        ignoring any alphas_cumprod stored in the checkpoint. This ensures
        consistent sigma→timestep mapping which is critical for correct
        UNet predictions.

        The checkpoint's own alphas_cumprod must never be used directly: some
        checkpoints (notably anime models) store a modified alphas_cumprod that
        produces a wrong sigma→timestep mapping and causes oversaturation.
        """
        if not hasattr(pipe.scheduler, 'alphas_cumprod'):
            return

        # Compute the CORRECT SDXL alphas_cumprod from standard betas.
        # Use float64 for precision in cumulative product, then convert to float32.
        # These values match ComfyUI's model_sampling.py exactly.
        beta_start = 0.00085
        beta_end = 0.012
        num_timesteps = 1000
        betas = torch.linspace(beta_start**0.5, beta_end**0.5, num_timesteps, dtype=torch.float64) ** 2
        alphas = 1.0 - betas
        standard_alphas_cumprod = torch.cumprod(alphas, dim=0).float()

        scheduler_alphas = pipe.scheduler.alphas_cumprod.float()
        standard_sigma_max = ((1 - standard_alphas_cumprod[-1]) / standard_alphas_cumprod[-1]) ** 0.5
        scheduler_sigma_max = ((1 - scheduler_alphas[-1]) / scheduler_alphas[-1]) ** 0.5

        logger.debug(f"[MODEL][SDXL] Standard SDXL: alpha[-1]={standard_alphas_cumprod[-1]:.6f}, "
                    f"sigma_max={standard_sigma_max:.4f}")
        logger.debug(f"[MODEL][SDXL] Scheduler:     alpha[-1]={scheduler_alphas[-1]:.6f}, "
                    f"sigma_max={scheduler_sigma_max:.4f}")

        # Check if scheduler values match standard SDXL
        max_diff = (standard_alphas_cumprod - scheduler_alphas).abs().max().item()
        if max_diff > 1e-4:
            logger.warning(f"[MODEL][SDXL] Scheduler alphas_cumprod differs from standard SDXL "
                          f"(max_diff={max_diff:.6f}). Replacing with standard values.")
            device = scheduler_alphas.device
            pipe.scheduler.alphas_cumprod = standard_alphas_cumprod.to(device)

            # Also update scheduler's internal sigmas buffer so init_noise_sigma stays consistent.
            # Without this, init_noise_sigma uses stale sigmas from the original (wrong) alphas.
            new_sigmas = ((1 - standard_alphas_cumprod) / standard_alphas_cumprod) ** 0.5
            # Scheduler sigmas are descending (sigma_max first), with trailing zero
            pipe.scheduler.sigmas = torch.cat([new_sigmas.flip(0), torch.zeros(1)]).to(device)
            logger.debug(f"[MODEL][SDXL] Updated scheduler sigmas: max={pipe.scheduler.sigmas[0]:.4f}, "
                        f"init_noise_sigma={pipe.scheduler.init_noise_sigma:.4f}")
        else:
            logger.debug(f"[MODEL][SDXL] Scheduler alphas_cumprod matches standard SDXL (OK, max_diff={max_diff:.8f})")

    def _seed_sampler_noise_stream(self, seed: int):
        """Seed the GLOBAL torch RNG (used by k-diffusion's ancestral/SDE
        samplers via torch.randn_like) deterministically, but on a DIFFERENT
        stream than the torch.Generator that draws the initial latents.

        Seeding both streams with the same value makes the sampler's first
        noise draw duplicate the initial latent noise exactly — perfectly
        correlated noise that accumulates instead of averaging out, burning
        ancestral-sampler outputs (EULER_A, DPM2_A, DPMPP_2S_A). Plain ODE
        samplers (EULER, DPMPP_2M) never draw from this stream.
        """
        noise_seed = (int(seed) * 6364136223846793005 + 1442695040888963407) % (2 ** 63)
        torch.manual_seed(noise_seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(noise_seed)

    def _determine_vae_dtype(self):
        """Determine optimal VAE dtype. ComfyUI/Forge use float32 VAE to prevent overflow in decoder exponential ops."""
        vae_dtype_config = self.config.get("extras", {}).get("vae_dtype", "auto")

        if vae_dtype_config == "float32":
            return torch.float32
        elif vae_dtype_config == "bfloat16":
            return torch.bfloat16
        elif vae_dtype_config == "float16":
            return torch.float16

        # Auto mode: always use float32 for VAE decode precision
        # Both ComfyUI and Forge default to float32 VAE. bfloat16 has only 7 mantissa bits
        # which causes color quality degradation through the decoder's many layers.
        logger.debug("[MODEL][SDXL] Using float32 for VAE (matches ComfyUI/Forge for best quality)")
        return torch.float32

    def _apply_memory_strategy(self, pipe):
        """Apply VRAM-tier memory optimizations via the shared MemoryPolicy.

        `extras.memory_strategy` (cpu_offload / sequential_offload / gpu_only)
        forces the offload decision regardless of the policy tier.
        """
        if not torch.cuda.is_available():
            logger.debug("[MODEL][SDXL] CUDA not available, model will run on CPU")
            return

        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        vram_limit_gb = self.config.get("vram_limit_gb", None)
        if vram_limit_gb is None:
            vram_limit_gb = vram_gb
            logger.debug(f"[MODEL][SDXL] Available VRAM: {vram_gb:.2f} GB (auto-detected)")
        else:
            logger.debug(f"[MODEL][SDXL] Available VRAM: {vram_gb:.2f} GB, User limit: {vram_limit_gb:.2f} GB")

        memory_strategy = self.config.get("extras", {}).get("memory_strategy", "auto")
        offload_override = {
            "cpu_offload": "model",
            "sequential_offload": "sequential",
            "gpu_only": "none",
        }.get(memory_strategy)
        if memory_strategy not in ("auto", "cpu_offload", "sequential_offload", "gpu_only"):
            logger.warning(f"[MODEL][SDXL] Unknown memory_strategy '{memory_strategy}', defaulting to auto")

        apply_to_pipeline(
            pipe,
            MemoryPolicy(vram_limit_gb),
            offload_override=offload_override,
            use_xformers=self.config.get("extras", {}).get("use_xformers", False),
        )

    def _load_extras(self, pipe):
        # FreeU
        if self.config.get("extras", {}).get("freeu", False):
            logger.debug("[MODEL][SDXL] Enabling FreeU")
            pipe.enable_freeu(s1=0.9, s2=0.2, b1=1.3, b2=1.4)

    def _load_loras(self, pipe):
        # Filter out zero-weight LoRAs upfront
        active_loras = [
            lora for lora in self.config.get("loras", [])
            if lora["weight"] != "" and lora["weight"] is not None and float(lora["weight"]) != 0
        ]

        if not active_loras:
            logger.debug("[MODEL][SDXL] No active LoRAs to load")
            return pipe

        loaded_loras = []

        # Suppress PEFT warning about multiple adapters - we know what we're doing
        import warnings
        warnings.filterwarnings('ignore', message='.*Already found a `peft_config` attribute.*')

        # Load each LoRA
        for lora in active_loras:
            try:
                lora_path = str(lora["file_path"])

                # Handle both directory paths and direct file paths
                if os.path.isfile(lora_path):
                    # Direct file path: split into directory and filename
                    lora_dir = os.path.dirname(lora_path)
                    weight_name = os.path.basename(lora_path)
                    adapter_name = os.path.splitext(weight_name)[0]
                else:
                    # Directory path: use as-is
                    lora_dir = lora_path
                    weight_name = "lora.safetensors"
                    adapter_name = os.path.basename(lora_dir)

                # Sanitize adapter name: PyTorch modules can't contain dots or other special chars
                # Replace dots, spaces, and other problematic characters with underscores
                adapter_name = adapter_name.replace(".", "_").replace(" ", "_").replace("-", "_")

                logger.debug(f"[MODEL][SDXL] Loading LoRA: {lora_path} (adapter: {adapter_name}, weight_name: {weight_name}) with strength {lora['weight']}")

                # Load LoRA weights (for inference only)
                pipe.load_lora_weights(
                    lora_dir,
                    adapter_name=adapter_name,
                    weight_name=weight_name,
                    local_files_only=True
                )
                loaded_loras.append({
                    "name": adapter_name,
                    "weight": float(lora["weight"])
                })

            except Exception as e:
                logger.error(
                    f"[MODEL][SDXL] Failed to load LoRA {lora['file_path']}: {str(e)}",
                    exc_info=True
                )
                continue

        if loaded_loras:
            try:
                logger.debug(f"[MODEL][SDXL] Loaded LoRAs: {[l['name'] for l in loaded_loras]}")
                pipe.set_adapters(
                    adapter_names=[l["name"] for l in loaded_loras],
                    adapter_weights=[l["weight"] for l in loaded_loras]
                )
                logger.debug(f"[MODEL][SDXL] Successfully activated {len(loaded_loras)} LoRAs")
            except Exception as e:
                logger.error(f"[MODEL][SDXL] Failed to set adapter weights: {str(e)}", exc_info=True)

        return pipe

    @torch.no_grad()
    def load(self, mode: str = "txt2img", force_reload: bool = False):
        """Load model for text-to-image or image-to-image generation."""
        logger.info(f"[MODEL][SDXL] Loading model from {self.config.get('path')}")

        # If pipeline already loaded and no force reload, just return
        if self.pipe is not None and not force_reload:
            self.loaded_pipe_type = "txt2img" if mode == "txt2img" else "img2img"
            return

        # If force_reload, clean up existing pipeline
        if self.pipe is not None and force_reload:
            logger.debug("[MODEL][SDXL] Force reloading model with new configuration")

            # Unload LoRAs before deleting pipeline
            try:
                if hasattr(self.pipe, 'unload_lora_weights'):
                    self.pipe.unload_lora_weights()
                    logger.debug("[MODEL][SDXL] Unloaded LoRA weights")
            except Exception as e:
                logger.warning(f"[MODEL][SDXL] Could not unload LoRA weights: {e}")

            del self.pipe
            self.pipe = None

            # Clean up ControlNet state when switching to standard pipeline
            if self.using_controlnet:
                logger.debug("[MODEL][SDXL] Cleaning up ControlNet state when switching to standard pipeline")
                if self.controlnets is not None:
                    for cn_data in self.controlnets:
                        if 'model' in cn_data and cn_data['model'] is not None:
                            del cn_data['model']
                    del self.controlnets
                    self.controlnets = None
                self.using_controlnet = False

            self.clear_cuda_cache()

        # Create the pipeline. `config=` points at the vendored local pipeline
        # config directory so config resolution never touches the HF Hub (see
        # SDXL_BASE_PIPELINE_CONFIG above).
        pipe = StableDiffusionXLKDiffusionPipeline.from_single_file(
            pretrained_model_link_or_path=self.config.get("path"),
            config=SDXL_BASE_PIPELINE_CONFIG,
            torch_dtype=torch.float32 if self.config.get("dtype") == "float32" else torch.float16,
            safety_checker=None if self.config.get("nsfw") else "safe",
            requires_safety_checker=not self.config.get("nsfw", True),
            use_safetensors=True,
        )

        # Upcast VAE to higher precision to prevent overflow in decoder exponential ops
        # This matches ComfyUI/Forge behavior and fixes color banding/oversaturation
        vae_dtype = self._determine_vae_dtype()
        pipe.vae = pipe.vae.to(dtype=vae_dtype)
        logger.debug(f"[MODEL][SDXL] VAE dtype set to {vae_dtype} (prevents decoder overflow)")

        if hasattr(pipe, 'scheduler'):
            logger.debug(f"[MODEL][SDXL] Scheduler: {type(pipe.scheduler).__name__}")
            if hasattr(pipe.scheduler, 'config'):
                config = pipe.scheduler.config
                logger.debug(f"[MODEL][SDXL] Scheduler config: prediction_type={getattr(config, 'prediction_type', 'epsilon')}, "
                            f"beta_end={getattr(config, 'beta_end', 'N/A')}, "
                            f"rescale_betas_zero_snr={getattr(config, 'rescale_betas_zero_snr', False)}")
                logger.debug(f"[MODEL][SDXL] Scheduler full config: beta_start={getattr(config, 'beta_start', 'N/A')}, "
                             f"beta_schedule={getattr(config, 'beta_schedule', 'N/A')}, "
                             f"num_train_timesteps={getattr(config, 'num_train_timesteps', 'N/A')}, "
                             f"timestep_spacing={getattr(config, 'timestep_spacing', 'N/A')}")

            # Check if alphas_cumprod exists directly on the scheduler
            if hasattr(pipe.scheduler, 'alphas_cumprod'):
                alphas = pipe.scheduler.alphas_cumprod
                logger.debug(f"[MODEL][SDXL] Scheduler has alphas_cumprod: shape={alphas.shape}, min={alphas.min():.6f}, max={alphas.max():.6f}")
                logger.debug(f"[MODEL][SDXL] Scheduler alphas_cumprod[0]={alphas[0]:.6f}, alphas_cumprod[-1]={alphas[-1]:.6f}")
            else:
                logger.warning("[MODEL][SDXL] Scheduler does NOT have alphas_cumprod attribute")

        if hasattr(pipe, 'vae') and hasattr(pipe.vae, 'config'):
            logger.debug(f"[MODEL][SDXL] VAE scaling_factor: {getattr(pipe.vae.config, 'scaling_factor', 'unknown')}")
            logger.debug(f"[MODEL][SDXL] VAE sample_size: {getattr(pipe.vae.config, 'sample_size', 'unknown')}")

        # Check if the checkpoint stores its own alphas_cumprod/betas that differ from
        # what from_single_file() computed. from_single_file() hardcodes beta_start=0.00085,
        # beta_end=0.012 for SDXL but the model may have been trained with different values.
        self._check_and_fix_scheduler_from_checkpoint(pipe)

        # Detect model type for smart defaults (must run before ZTSNR to inform rescaling decision)
        self.model_type_info = SDXLModelTypeDetector.detect(pipe)

        # Log terminal SNR info - no rescaling applied
        # ZTSNR models are handled safely by epsilon clamping in k-diffusion/external.py
        # (alphas_cumprod clamped to [1e-6, 1-1e-6] before sigma calculation)
        # Rescaling was removed because it inflated sigma_max (~45 → ~1000) causing oversaturation
        if hasattr(pipe.scheduler, 'alphas_cumprod'):
            alphas = pipe.scheduler.alphas_cumprod
            terminal_snr = alphas[-1].item()
            logger.debug(f"[MODEL][SDXL] Terminal SNR = {terminal_snr:.6f}, "
                       f"ZTSNR={self.model_type_info.uses_ztsnr} "
                       f"(no rescaling - epsilon clamping in k-diffusion handles ZTSNR safely)")

        # Move pipeline to correct device
        device = "cuda" if self.config.get("device") == "cuda" and torch.cuda.is_available() else "cpu"
        self._inference_device = device  # Store for generator creation (sequential offload sets pipe.device to "meta")
        pipe = pipe.to(device)

        # Apply optimizations
        self._apply_memory_strategy(pipe)

        # Extras: e.g. FreeU, etc.
        self._load_extras(pipe)

        # LoRA injection
        self._load_loras(pipe)

        self.pipe = pipe

        # We'll clear cache once after loading
        self.clear_cuda_cache()

        # Log memory usage after model loading
        log_memory_usage("AFTER_MODEL_LOAD", prefix="[MODEL][SDXL]")

    @torch.no_grad()
    def load_with_controlnet(self, controlnet_data: list, mode: str = "txt2img", force_reload: bool = False):
        """Load model with ControlNet support using K-Diffusion pipeline.

        Args:
            controlnet_data: List of dicts containing controlnet models and config
                Each dict should have: {'model': ControlNetModel, 'conditioning_scale': float, ...}
            mode: Generation mode (txt2img or img2img)
            force_reload: Whether to force reload the pipeline
        """
        if not DIFFUSERS_CONTROLNET_AVAILABLE:
            raise ValueError("[MODEL][SDXL] ControlNet support requires standard diffusers library")

        if not controlnet_data:
            logger.warning("[MODEL][SDXL] load_with_controlnet called with no controlnet data, falling back to standard load")
            return self.load(mode=mode, force_reload=force_reload)

        logger.info(f"[MODEL][SDXL] Loading model with {len(controlnet_data)} ControlNet(s)")

        # If pipeline exists and is already using ControlNet, just return
        if self.pipe is not None and self.using_controlnet and not force_reload:
            return

        # If we already have a non-controlnet pipeline loaded, clean it up
        if self.pipe is not None and not self.using_controlnet:
            logger.debug("[MODEL][SDXL] Switching from standard pipeline to K-Diffusion ControlNet pipeline")

            # Unload LoRAs before deleting pipeline
            try:
                if hasattr(self.pipe, 'unload_lora_weights'):
                    self.pipe.unload_lora_weights()
                    logger.debug("[MODEL][SDXL] Unloaded LoRA weights before switching to ControlNet")
            except Exception as e:
                logger.warning(f"[MODEL][SDXL] Could not unload LoRA weights: {e}")

            del self.pipe
            self.pipe = None
            self.clear_cuda_cache()

        # If force reload or no pipeline loaded yet
        if self.pipe is None or force_reload:
            if self.pipe is not None:
                # Unload LoRAs before force reload
                try:
                    if hasattr(self.pipe, 'unload_lora_weights'):
                        self.pipe.unload_lora_weights()
                        logger.debug("[MODEL][SDXL] Unloaded LoRA weights before force reload")
                except Exception as e:
                    logger.warning(f"[MODEL][SDXL] Could not unload LoRA weights: {e}")

                del self.pipe
                self.pipe = None
                self.clear_cuda_cache()

            # Clean up old ControlNet references to prevent memory leaks
            if self.controlnets is not None:
                logger.debug("[MODEL][SDXL] Cleaning up old ControlNet references")
                # Delete the ControlNet models from previous loads
                for cn_data in self.controlnets:
                    if 'model' in cn_data and cn_data['model'] is not None:
                        del cn_data['model']
                del self.controlnets
                self.controlnets = None
                self.clear_cuda_cache()

            # Extract ControlNet models from the data
            controlnet_models = [cn_data['model'] for cn_data in controlnet_data]

            # Use MultiControlNet if multiple controlnets, otherwise single
            if len(controlnet_models) > 1:
                controlnet = MultiControlNetModel(controlnet_models)
                logger.debug(f"[MODEL][SDXL] Using MultiControlNet with {len(controlnet_models)} models")
            else:
                controlnet = controlnet_models[0]
                logger.debug("[MODEL][SDXL] Using single ControlNet")

            # Create K-Diffusion pipeline with ControlNet support. `config=` points
            # at the vendored local pipeline config directory so config resolution
            # never touches the HF Hub (see SDXL_BASE_PIPELINE_CONFIG above).
            logger.debug("[MODEL][SDXL] Creating K-Diffusion pipeline with ControlNet")
            pipe = StableDiffusionXLKDiffusionPipeline.from_single_file(
                pretrained_model_link_or_path=self.config.get("path"),
                config=SDXL_BASE_PIPELINE_CONFIG,
                controlnet=controlnet,
                torch_dtype=torch.float32 if self.config.get("dtype") == "float32" else torch.float16,
                safety_checker=None if self.config.get("nsfw") else "safe",
                requires_safety_checker=not self.config.get("nsfw", True),
                use_safetensors=True,
            )

            # Upcast VAE to higher precision to prevent overflow in decoder exponential ops
            vae_dtype = self._determine_vae_dtype()
            pipe.vae = pipe.vae.to(dtype=vae_dtype)
            logger.debug(f"[MODEL][SDXL] VAE dtype set to {vae_dtype} (prevents decoder overflow)")

            # Move to device
            device = "cuda" if self.config.get("device") == "cuda" and torch.cuda.is_available() else "cpu"
            self._inference_device = device  # Store for generator creation (sequential offload sets pipe.device to "meta")
            pipe = pipe.to(device)

            # Apply optimizations
            self._apply_memory_strategy(pipe)

            # Extras: e.g. FreeU, etc.
            self._load_extras(pipe)

            # Load LoRAs
            self._load_loras(pipe)

            self.pipe = pipe
            self.controlnets = controlnet_data  # Store for later reference
            self.using_controlnet = True
            self.clear_cuda_cache()

            logger.debug("[MODEL][SDXL] K-Diffusion ControlNet pipeline loaded successfully")

    @torch.no_grad()
    def _make_step_callback(self, generation_input: GenerationInput, generation_outputs: callable, state: str, total_steps: int):
        """Build the callback_on_step_end closure shared by all four generation methods.

        Emits a live latent preview every 5 steps and a progress update every step.
        """
        def step_callback(pipe, step, timestep, callback_kwargs):
            with torch.no_grad():
                width, height = generation_input[IOType.RESOLUTION]

                if step % 5 == 0:
                    image = latents_to_rgb(callback_kwargs["latents"][0], width, height)
                    generation_outputs(ImageGenerationOutput(image=image))

                generation_outputs(
                    ProgressGenerationOutput(
                        title="generator",
                        state=state,
                        progress=Progress(step + 1, total_steps),
                    )
                )

                return callback_kwargs
        return step_callback

    def _resolve_guidance_rescale(self, generation_input: GenerationInput) -> float:
        """Resolve guidance_rescale, auto-applying the model's recommended value
        (e.g. ZTSNR anime models) when the user left it at the default of 0.0.
        """
        guidance_rescale = generation_input.get(IOType.GUIDANCE_RESCALE, 0.0)
        if guidance_rescale == 0.0 and self.model_type_info and self.model_type_info.recommended_guidance_rescale > 0:
            guidance_rescale = self.model_type_info.recommended_guidance_rescale
            logger.debug(f"[MODEL][SDXL] Auto-applied guidance_rescale={guidance_rescale} for {self.model_type_info.model_style} model")
        return guidance_rescale

    def txt2img_controlnet(self, generation_input: GenerationInput, control_images: list, generation_outputs: callable):
        """
        Generate an image from text using ControlNet with K-Diffusion pipeline.

        Args:
            generation_input: Standard generation input parameters
            control_images: List of control images (PIL Images)
            generation_outputs: Callback for generation outputs
        """
        if not self.using_controlnet:
            raise ValueError("[MODEL][SDXL] txt2img_controlnet called but not using controlnet pipeline")

        conditioning: ConditioningModel = generation_input.get(IOType.CONDITIONING)

        # Prepare controlnet conditioning scales
        # If we have multiple controlnets, we need a list of scales
        if self.controlnets and len(self.controlnets) > 1:
            conditioning_scales = [cn['conditioning_scale'] for cn in self.controlnets]
            control_guidance_start = [cn['control_guidance_start'] for cn in self.controlnets]
            control_guidance_end = [cn['control_guidance_end'] for cn in self.controlnets]
        else:
            # Single controlnet - use single value
            conditioning_scales = self.controlnets[0]['conditioning_scale'] if self.controlnets else 1.0
            control_guidance_start = self.controlnets[0]['control_guidance_start'] if self.controlnets else 0.0
            control_guidance_end = self.controlnets[0]['control_guidance_end'] if self.controlnets else 1.0

        # Prepare control image(s)
        # For multiple controlnets, we need multiple control images
        if len(self.controlnets) > 1:
            # Use all provided control images (one per controlnet)
            controlnet_images = control_images[:len(self.controlnets)]
        else:
            # Single controlnet - use first image
            controlnet_images = control_images[0] if control_images else None

        # Simple step callback for progress
        step_callback = self._make_step_callback(
            generation_input, generation_outputs, "TXT2IMG (ControlNet)", generation_input[IOType.STEP]
        )

        # Seed the sampler's noise stream (decorrelated from initial latents)
        seed = generation_input[IOType.SEED]
        self._seed_sampler_noise_stream(seed)

        # Get sampler algorithm and noise schedule
        sampler_name = generation_input.get(IOType.SAMPLER, "DPMPP_2M")
        sampler = SDXLParameterAdapter.SAMPLER_MAP.get(sampler_name.upper(), "dpmpp_2m")
        scheduler = generation_input.get(IOType.SCHEDULER, "karras")
        guidance_rescale = self._resolve_guidance_rescale(generation_input)

        logger.debug(f"[MODEL][SDXL] Generating with ControlNet - conditioning_scale: {conditioning_scales}, sampler: {sampler}, scheduler: {scheduler}")

        with torch.no_grad():
            output = self.pipe(
                prompt_embeds=conditioning.embeds["embeds"],
                negative_prompt_embeds=conditioning.n_embeds["embeds"],
                pooled_prompt_embeds=conditioning.embeds["pooled"],
                negative_pooled_prompt_embeds=conditioning.n_embeds["pooled"],
                control_image=controlnet_images,
                controlnet_conditioning_scale=conditioning_scales,
                control_guidance_start=control_guidance_start,
                control_guidance_end=control_guidance_end,
                num_inference_steps=generation_input[IOType.STEP],
                guidance_scale=generation_input[IOType.CFG],
                guidance_rescale=guidance_rescale,
                width=generation_input[IOType.RESOLUTION][0],
                height=generation_input[IOType.RESOLUTION][1],
                generator=torch.Generator(device=self._inference_device).manual_seed(seed),
                num_images_per_prompt=1,
                output_type="pil",
                sampler=sampler,
                scheduler=scheduler,
                hooks=self.get_ordered_hooks(),
                callback_on_step_end=step_callback,
                callback_on_step_end_tensor_inputs=["latents"],
                clip_skip=generation_input[IOType.CLIP_SKIP],
            )

        image = output.images[0] if output.images else None
        if image is None:
            raise ValueError("[MODEL][SDXL] ControlNet image generation failed")

        generation_outputs(ImageGenerationOutput(image=image))

        # Light cleanup between generations; the pipe layer runs one
        # aggressive cleanup per pipe run (aggressive = sync + 3 GC passes,
        # too costly to repeat per image/tile)
        self.clear_cuda_cache()

        return ImageGenerationOutput(image=image)

    @torch.no_grad()
    def img2img_controlnet(self, generation_input: GenerationInput, control_images: list, generation_outputs: callable):
        """
        Modify an existing image using ControlNet with K-Diffusion pipeline.

        Args:
            generation_input: Standard generation input parameters
            control_images: List of control images (PIL Images)
            generation_outputs: Callback for generation outputs
        """
        if not self.using_controlnet:
            raise ValueError("[MODEL][SDXL] img2img_controlnet called but not using controlnet pipeline")

        conditioning: ConditioningModel = generation_input.get(IOType.CONDITIONING)

        # Prepare controlnet conditioning scales (same as txt2img_controlnet)
        if self.controlnets and len(self.controlnets) > 1:
            conditioning_scales = [cn['conditioning_scale'] for cn in self.controlnets]
            control_guidance_start = [cn['control_guidance_start'] for cn in self.controlnets]
            control_guidance_end = [cn['control_guidance_end'] for cn in self.controlnets]
        else:
            conditioning_scales = self.controlnets[0]['conditioning_scale'] if self.controlnets else 1.0
            control_guidance_start = self.controlnets[0]['control_guidance_start'] if self.controlnets else 0.0
            control_guidance_end = self.controlnets[0]['control_guidance_end'] if self.controlnets else 1.0

        # Prepare control image(s)
        if len(self.controlnets) > 1:
            controlnet_images = control_images[:len(self.controlnets)]
        else:
            controlnet_images = control_images[0] if control_images else None

        # Get input image for img2img
        input_image = generation_input.get(IOType.IMAGE, None)
        if input_image is None:
            raise ValueError("[MODEL][SDXL] img2img requires input image")

        max_steps = int(generation_input[IOType.STEP] * generation_input[IOType.DENOISE])
        step_callback = self._make_step_callback(
            generation_input, generation_outputs, "IMG2IMG (ControlNet)", max_steps
        )

        # Seed the sampler's noise stream (decorrelated from initial latents)
        seed = generation_input[IOType.SEED]
        self._seed_sampler_noise_stream(seed)

        # Get sampler algorithm and noise schedule
        sampler_name = generation_input.get(IOType.SAMPLER, "DPMPP_2M")
        sampler = SDXLParameterAdapter.SAMPLER_MAP.get(sampler_name.upper(), "dpmpp_2m")
        scheduler = generation_input.get(IOType.SCHEDULER, "karras")
        guidance_rescale = self._resolve_guidance_rescale(generation_input)

        logger.debug(f"[MODEL][SDXL] img2img with ControlNet - conditioning_scale: {conditioning_scales}, sampler: {sampler}, scheduler: {scheduler}")

        with torch.no_grad():
            output = self.pipe(
                prompt_embeds=conditioning.embeds["embeds"],
                negative_prompt_embeds=conditioning.n_embeds["embeds"],
                pooled_prompt_embeds=conditioning.embeds["pooled"],
                negative_pooled_prompt_embeds=conditioning.n_embeds["pooled"],
                control_image=controlnet_images,
                controlnet_conditioning_scale=conditioning_scales,
                control_guidance_start=control_guidance_start,
                control_guidance_end=control_guidance_end,
                image=input_image,
                strength=generation_input[IOType.DENOISE],
                num_inference_steps=generation_input[IOType.STEP],
                guidance_scale=generation_input[IOType.CFG],
                guidance_rescale=guidance_rescale,
                width=generation_input[IOType.RESOLUTION][0],
                height=generation_input[IOType.RESOLUTION][1],
                generator=torch.Generator(device=self._inference_device).manual_seed(seed),
                num_images_per_prompt=1,
                output_type="pil",
                sampler=sampler,
                scheduler=scheduler,
                hooks=self.get_ordered_hooks(),
                callback_on_step_end=step_callback,
                callback_on_step_end_tensor_inputs=["latents"],
                clip_skip=generation_input[IOType.CLIP_SKIP],
            )

        image = output.images[0] if output.images else None
        if image is None:
            raise ValueError("[MODEL][SDXL] ControlNet img2img generation failed")

        generation_outputs(ImageGenerationOutput(image=image))

        # Light cleanup between generations (see txt2img note)
        self.clear_cuda_cache()

        return ImageGenerationOutput(image=image)

    @torch.no_grad()
    def txt2img(self, generation_input: GenerationInput, generation_outputs: callable):
        """
        Generate an image from text using the loaded pipeline.
        """

        conditioning: ConditioningModel = generation_input.get(IOType.CONDITIONING)

        # Step callback for progress reporting
        step_callback = self._make_step_callback(
            generation_input, generation_outputs, "TXT2IMG", generation_input[IOType.STEP]
        )

        # Debug: Check conditioning embeddings statistics (the f-strings force
        # GPU syncs, so the reductions themselves must be gated on DEBUG)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[MODEL][SDXL] Positive embeds - mean: {conditioning.embeds['embeds'].mean():.4f}, std: {conditioning.embeds['embeds'].std():.4f}")
            logger.debug(f"[MODEL][SDXL] Negative embeds - mean: {conditioning.n_embeds['embeds'].mean():.4f}, std: {conditioning.n_embeds['embeds'].std():.4f}")

        # Check for extreme values that might cause issues with high CFG
        # (compute each max once and reuse)
        pos_abs_max = conditioning.embeds["embeds"].abs().max()
        if pos_abs_max > 100:
            logger.warning(f"[MODEL][SDXL] Very large values in positive embeds: max={pos_abs_max:.2f}")
        neg_abs_max = conditioning.n_embeds["embeds"].abs().max()
        if neg_abs_max > 100:
            logger.warning(f"[MODEL][SDXL] Very large values in negative embeds: max={neg_abs_max:.2f}")

        # Get guidance_rescale from generation input, auto-apply for ZTSNR anime models
        guidance_rescale = self._resolve_guidance_rescale(generation_input)

        # Seed the sampler's noise stream (decorrelated from initial latents)
        seed = generation_input[IOType.SEED]
        self._seed_sampler_noise_stream(seed)

        # Log memory before generation
        log_memory_usage("BEFORE_TXT2IMG", prefix="[MODEL][SDXL]")

        # Use parameter adapter to build pipeline parameters
        adapter = SDXLParameterAdapter(generation_input)
        params = adapter.build_pipeline_params(conditioning, mode="txt2img")

        # Add callback and guidance_rescale (adapter doesn't handle these yet)
        params["callback_on_step_end"] = step_callback
        params["callback_on_step_end_tensor_inputs"] = ["latents"]
        params["guidance_rescale"] = guidance_rescale
        params["hooks"] = self.get_ordered_hooks()

        with torch.no_grad():
            output = self.pipe(**params)

        # Log memory after generation (before cleanup)
        log_memory_usage("AFTER_TXT2IMG_BEFORE_CLEANUP", prefix="[MODEL][SDXL]")

        image = output.images[0] if output.images else None
        if image is None:
            raise ValueError("[MODEL][SDXL] Image generation failed")

        generation_outputs(ImageGenerationOutput(image=image))

        # Light cleanup between generations; the pipe layer runs one
        # aggressive cleanup per pipe run (aggressive = sync + 3 GC passes,
        # too costly to repeat per image/tile)
        self.clear_cuda_cache()

        # Log memory after cleanup
        log_memory_usage("AFTER_TXT2IMG_AFTER_CLEANUP", prefix="[MODEL][SDXL]")

        return ImageGenerationOutput(image=image)

    @torch.no_grad()
    def img2img(self, generation_input: GenerationInput, generation_outputs: callable):
        """
        Modify an existing image using the loaded pipeline.
        """

        conditioning: ConditioningModel = generation_input.get(IOType.CONDITIONING)

        max_steps = int(generation_input[IOType.STEP] * generation_input[IOType.DENOISE])
        step_callback = self._make_step_callback(generation_input, generation_outputs, "IMG2IMG", max_steps)

        # Removed redundant CPU-GPU transfer that caused memory leaks
        output_type = generation_input.get(IOType.IMAGE_TYPE, "pil")
        # Map custom user types to actual pipe output_type
        if output_type == "NUMPY":
            output_type = "np.array"
        elif output_type == "LATENT":
            output_type = "latent"

        image = generation_input.get(IOType.IMAGE, None)
        mask = generation_input.get(IOType.MASK, None)

        if image is None:
            image = generation_input.get(IOType.NUMPY, None)

        if image is None:
            raise ValueError("[MODEL][SDXL] Missing image or numpy data")

        # Get guidance_rescale from generation input, auto-apply for ZTSNR anime models
        guidance_rescale = self._resolve_guidance_rescale(generation_input)

        # Seed the sampler's noise stream (decorrelated from initial latents)
        seed = generation_input[IOType.SEED]
        self._seed_sampler_noise_stream(seed)

        # Use parameter adapter to build pipeline parameters
        adapter = SDXLParameterAdapter(generation_input)
        params = adapter.build_pipeline_params(conditioning, mode="img2img")

        # Add callback, guidance_rescale, and output_type (adapter doesn't handle these yet)
        params["callback_on_step_end"] = step_callback
        params["callback_on_step_end_tensor_inputs"] = ["latents"]
        params["guidance_rescale"] = guidance_rescale
        params["hooks"] = self.get_ordered_hooks()
        params["output_type"] = output_type

        with torch.no_grad():
            output = self.pipe.img2img(**params)

        image = output.images[0] if output.images else None
        if image is None:
            raise ValueError("[MODEL][SDXL] Image generation failed")

        generation_outputs(ImageGenerationOutput(image=image))

        # Light cleanup between generations/tiles (see txt2img note);
        # detailer pipes call an aggressive cleanup once per pipe run
        self.clear_cuda_cache()

        # Return format depends on user-specified output type
        if output_type == "latent":
            return GenerationOutput(ImageGenerationOutput(image=image))
        elif output_type == "np.array":
            return GenerationOutput(ImageGenerationOutput(image=image))

        return ImageGenerationOutput(image=image)

    def supports(self) -> BaseModel:
        return self.template.base

