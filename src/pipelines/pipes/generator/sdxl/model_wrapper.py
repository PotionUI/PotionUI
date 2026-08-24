"""
SDXLModelWrapper - K-diffusion compatible model wrapper for SDXL generation.

Clean model wrapper that handles core denoising: CFG + UNet call.
Enhancement features (ADM, SAG, Sharpness) are applied via DenoisingHook
instances registered on the model, not baked into this wrapper.

Hook lifecycle in apply_model():
    1. ControlNet (core - complex GPU/CPU management)
    2. on_pre_unet hooks (e.g., ADM modifies conditioning)
    3. UNet forward pass (with InpaintHead injection)
    4. on_post_unet hooks (e.g., SAG, Sharpness modify noise_pred)
    5. CFG application + guidance_rescale
    6. on_post_cfg hooks
"""

import logging as std_logging

from dataclasses import dataclass
from typing import Optional, Union, List
import torch
from diffusers.utils import logging

from src.platform.runtime.device import clear_gpu_memory
from src.pipelines.pipes.generator.sdxl.denoising_hook import DenoisingHook, DenoisingContext
from src.pipelines.pipes.generator.sdxl.inpaint_head import InpaintHeadLoader, prepare_inpaint_head_input

logger = logging.get_logger(__name__)


@dataclass
class ControlNetConfig:
    """Configuration for ControlNet."""
    controlnet: Optional[torch.nn.Module] = None
    control_image: Optional[Union[torch.Tensor, List[torch.Tensor]]] = None
    controlnet_conditioning_scale: Union[float, List[float]] = 1.0
    control_guidance_start: Union[float, List[float]] = 0.0
    control_guidance_end: Union[float, List[float]] = 1.0


@dataclass
class IPAdapterConfig:
    """Configuration for IP-Adapter."""
    ip_adapter_image: Optional[torch.Tensor] = None
    ip_adapter_image_embeds: Optional[List[torch.FloatTensor]] = None
    image_embeds: Optional[torch.Tensor] = None


@dataclass
class InpaintConfig:
    """Configuration for inpainting (Fooocus-style)."""
    init_latents: Optional[torch.Tensor] = None
    mask_image: Optional[torch.Tensor] = None
    noise: Optional[torch.Tensor] = None
    inpaint_head_model_path: Optional[str] = None


def rescale_noise_cfg(noise_cfg, noise_pred_text, guidance_rescale=0.0):
    """
    Rescale `noise_cfg` according to `guidance_rescale`. Based on findings of [Common Diffusion Noise Schedules and
    Sample Steps are Flawed](https://arxiv.org/pdf/2305.08891.pdf). See Section 3.4
    """
    std_text = noise_pred_text.std(dim=list(range(1, noise_pred_text.ndim)), keepdim=True)
    std_cfg = noise_cfg.std(dim=list(range(1, noise_cfg.ndim)), keepdim=True)
    noise_pred_rescaled = noise_cfg * (std_text / std_cfg)
    noise_cfg = guidance_rescale * noise_pred_rescaled + (1 - guidance_rescale) * noise_cfg
    return noise_cfg


class SDXLModelWrapper:
    """
    K-diffusion compatible model wrapper for SDXL generation.

    Core responsibilities (kept here):
    - Noise prediction through the UNet
    - Classifier-free guidance (CFG)
    - ControlNet integration with dynamic GPU/CPU offloading
    - Inpainting support (Fooocus-style InpaintHead injection)
    - IP-Adapter conditioning
    - guidance_rescale

    Extensible via hooks (moved out):
    - ADM Guidance -> on_pre_unet hook
    - Sharpness -> on_post_unet hook
    - SAG -> on_post_unet hook
    """

    def __init__(
        self,
        unet,
        scheduler,
        prompt_embeds,
        add_text_embeds,
        add_time_ids,
        num_inference_steps,
        guidance_scale: float,
        guidance_rescale: float = 0.0,
        do_classifier_free_guidance: bool = True,
        cross_attention_kwargs=None,
        controlnet_config: Optional[ControlNetConfig] = None,
        ip_adapter_config: Optional[IPAdapterConfig] = None,
        inpaint_config: Optional[InpaintConfig] = None,
        hooks: Optional[List[DenoisingHook]] = None,
    ):
        # Core components
        self.unet = unet
        self.scheduler = scheduler
        self.prompt_embeds = prompt_embeds
        self.cross_attention_kwargs = cross_attention_kwargs
        self.num_inference_steps = num_inference_steps
        self.current_step = 0

        # SDXL additional conditioning
        self.add_text_embeds = add_text_embeds
        self.add_time_ids = add_time_ids

        # Guidance parameters (direct, no wrapper dataclass)
        self.do_classifier_free_guidance = do_classifier_free_guidance
        self.guidance_scale = guidance_scale
        self.guidance_rescale = guidance_rescale

        # Hooks (sorted by priority, lower = runs first)
        self.hooks = sorted(hooks or [], key=lambda h: h.priority)
        if self.hooks:
            logger.debug(f"[MODEL WRAPPER] Registered {len(self.hooks)} hooks: {[h.name for h in self.hooks]}")

        # ControlNet configuration
        controlnet_config = controlnet_config or ControlNetConfig()
        self.controlnet = controlnet_config.controlnet
        self.control_image = controlnet_config.control_image
        self.controlnet_conditioning_scale = controlnet_config.controlnet_conditioning_scale
        self.control_guidance_start = controlnet_config.control_guidance_start
        self.control_guidance_end = controlnet_config.control_guidance_end

        # IP-Adapter configuration
        ip_adapter_config = ip_adapter_config or IPAdapterConfig()
        self.ip_adapter_image = ip_adapter_config.ip_adapter_image
        self.ip_adapter_image_embeds = ip_adapter_config.ip_adapter_image_embeds
        self.image_embeds = ip_adapter_config.image_embeds

        # Inpainting configuration (Fooocus-style)
        inpaint_config = inpaint_config or InpaintConfig()
        self.inpaint_head_feature = None
        if (inpaint_config.mask_image is not None and inpaint_config.init_latents is not None and
                inpaint_config.inpaint_head_model_path is not None):
            self._setup_inpaint_head(inpaint_config)

        # Track ControlNet device for dynamic offloading
        self.controlnet_device = None
        if self.controlnet is not None:
            self.controlnet_device = next(self.controlnet.parameters()).device
            self.controlnet.to('cpu')
        self.controlnet_on_gpu = False

        # Log conditioning statistics for CFG diagnosis.
        # Gated on DEBUG because the tensor reductions force GPU syncs and this
        # constructor runs once per generation / per detailer tile.
        if (self.do_classifier_free_guidance and add_text_embeds is not None
                and logger.isEnabledFor(std_logging.DEBUG)):
            neg_pooled, pos_pooled = add_text_embeds.chunk(2)
            pooled_diff = (pos_pooled - neg_pooled).abs().mean().item()
            logger.debug(f"[MODEL WRAPPER] Pooled embeds: pos_std={pos_pooled.std():.4f}, "
                         f"neg_std={neg_pooled.std():.4f}, diff_mean={pooled_diff:.4f}")
            if prompt_embeds is not None:
                neg_seq, pos_seq = prompt_embeds.chunk(2)
                seq_diff = (pos_seq - neg_seq).abs().mean().item()
                logger.debug(f"[MODEL WRAPPER] Sequence embeds: pos_std={pos_seq.std():.4f}, "
                             f"neg_std={neg_seq.std():.4f}, diff_mean={seq_diff:.4f}")

        # alphas_cumprod for k-diffusion compatibility
        self._setup_alphas_cumprod()

    def _setup_inpaint_head(self, inpaint_config):
        """Load and prepare InpaintHead if inpainting is enabled."""
        try:
            inpaint_head_model = InpaintHeadLoader.load_inpaint_head(inpaint_config.inpaint_head_model_path)

            SDXL_SCALE_FACTOR = 0.13025
            scaled_init_latents = inpaint_config.init_latents * SDXL_SCALE_FACTOR
            inpaint_input = prepare_inpaint_head_input(inpaint_config.mask_image, scaled_init_latents)

            device = next(self.unet.parameters()).device
            dtype = next(self.unet.parameters()).dtype
            inpaint_input = inpaint_input.to(device=device, dtype=dtype)
            inpaint_head_model = inpaint_head_model.to(device=device, dtype=dtype)

            with torch.no_grad():
                self.inpaint_head_feature = inpaint_head_model(inpaint_input)

            logger.debug(f"[INPAINTING] Enabled - mask shape: {inpaint_config.mask_image.shape}")
        except Exception as e:
            logger.error(f"[INPAINT HEAD] Failed to load or process: {e}")
            self.inpaint_head_feature = None

    def _setup_alphas_cumprod(self):
        """Setup alphas_cumprod from scheduler for hook usage."""
        if hasattr(self.scheduler, 'alphas_cumprod') and self.scheduler.alphas_cumprod is not None:
            self.alphas_cumprod = self.scheduler.alphas_cumprod
        else:
            logger.warning("[K-DIFFUSION] Scheduler missing alphas_cumprod, calculating from config")
            beta_start = getattr(self.scheduler.config, 'beta_start', 0.00085)
            beta_end = getattr(self.scheduler.config, 'beta_end', 0.012)
            beta_schedule = getattr(self.scheduler.config, 'beta_schedule', 'linear')
            num_train_timesteps = getattr(self.scheduler.config, 'num_train_timesteps', 1000)
            rescale_betas_zero_snr = getattr(self.scheduler.config, 'rescale_betas_zero_snr', False)

            if beta_schedule == "scaled_linear":
                betas = torch.linspace(beta_start**0.5, beta_end**0.5, num_train_timesteps, dtype=torch.float32) ** 2
            else:
                betas = torch.linspace(beta_start, beta_end, num_train_timesteps, dtype=torch.float32)

            if rescale_betas_zero_snr:
                alphas = 1.0 - betas
                alphas_cumprod = torch.cumprod(alphas, dim=0)
                alphas_cumprod_sqrt = alphas_cumprod.sqrt()
                alphas_cumprod_sqrt_0 = alphas_cumprod_sqrt[0].clone()
                alphas_cumprod_sqrt_T = alphas_cumprod_sqrt[-1].clone()
                self.alphas_cumprod = ((alphas_cumprod_sqrt - alphas_cumprod_sqrt_T) / (alphas_cumprod_sqrt_0 - alphas_cumprod_sqrt_T)) ** 2
            else:
                alphas = 1.0 - betas
                self.alphas_cumprod = torch.cumprod(alphas, dim=0)

    def _apply_controlnet(self, latent_model_input, timestep_cond):
        """Apply ControlNet and return residual samples. Returns (down_blocks, mid_block)."""
        if self.controlnet is None or self.control_image is None:
            return None, None

        progress = self.current_step / max(self.num_inference_steps, 1)

        # Check guidance window
        apply_controlnet = False
        if isinstance(self.control_guidance_start, list):
            for start, end in zip(self.control_guidance_start, self.control_guidance_end):
                if start <= progress <= end:
                    apply_controlnet = True
                    break
        else:
            apply_controlnet = self.control_guidance_start <= progress <= self.control_guidance_end

        if not apply_controlnet:
            if self.controlnet_on_gpu:
                self.controlnet.to('cpu')
                self.controlnet_on_gpu = False
                clear_gpu_memory()
            return None, None

        # Move ControlNet to GPU
        if not self.controlnet_on_gpu:
            self.controlnet.to(self.controlnet_device)
            self.controlnet_on_gpu = True

        # Prepare control image
        if isinstance(self.control_image, list):
            control_image_input = [img.to(self.controlnet_device) for img in self.control_image]
        else:
            control_image_input = self.control_image.to(self.controlnet_device)

        if self.do_classifier_free_guidance:
            if isinstance(control_image_input, list):
                control_image_input = [torch.cat([img] * 2) for img in control_image_input]
            else:
                control_image_input = torch.cat([control_image_input] * 2)

        controlnet_added_cond_kwargs = {
            "text_embeds": self.add_text_embeds,
            "time_ids": self.add_time_ids
        }

        down_block_res_samples, mid_block_res_sample = self.controlnet(
            latent_model_input, timestep_cond,
            encoder_hidden_states=self.prompt_embeds,
            controlnet_cond=control_image_input,
            conditioning_scale=self.controlnet_conditioning_scale,
            added_cond_kwargs=controlnet_added_cond_kwargs,
            return_dict=False,
        )

        del control_image_input
        clear_gpu_memory()

        return down_block_res_samples, mid_block_res_sample

    def apply_model(self, x, t, **kwargs):
        """
        Apply the SDXL UNet model with hook-based extensible architecture.

        Called by CompVisDenoiser/CompVisVDenoiser from k-diffusion.
        't' is already the converted timestep (not sigma).
        """
        model_dtype = next(self.unet.parameters()).dtype
        latents_input = x.to(model_dtype)

        # Diagnostic logging for first few steps. Gated on DEBUG because each
        # tensor reduction forces a GPU sync inside the sampling hot loop.
        _log_step = ((self.current_step in (0, 1, 2) or self.current_step == self.num_inference_steps - 1)
                     and logger.isEnabledFor(std_logging.DEBUG))
        if _log_step:
            logger.debug(f"[STEP {self.current_step}/{self.num_inference_steps}] Input x: "
                         f"min={latents_input.min():.4f}, max={latents_input.max():.4f}, "
                         f"std={latents_input.std():.4f}")

        if t.ndim > 0:
            t = t[0]

        # Prepare for CFG
        latent_model_input = latents_input
        if self.do_classifier_free_guidance:
            latent_model_input = torch.cat([latent_model_input] * 2)
        timestep_cond = t.expand(latent_model_input.shape[0]).to(model_dtype)

        # ControlNet (stays in core - complex GPU/CPU management)
        down_block_res_samples, mid_block_res_sample = self._apply_controlnet(latent_model_input, timestep_cond)

        # Build context for hooks
        ctx = DenoisingContext(
            unet=self.unet,
            latent_model_input=latent_model_input,
            timestep=timestep_cond,
            noise_pred=None,
            current_step=self.current_step,
            total_steps=self.num_inference_steps,
            progress=self.current_step / max(self.num_inference_steps, 1),
            prompt_embeds=self.prompt_embeds,
            add_text_embeds=self.add_text_embeds,
            add_time_ids=self.add_time_ids,
            cross_attention_kwargs=self.cross_attention_kwargs,
            do_cfg=self.do_classifier_free_guidance,
            guidance_scale=self.guidance_scale,
            alphas_cumprod=self.alphas_cumprod,
            down_block_res_samples=down_block_res_samples,
            mid_block_res_sample=mid_block_res_sample,
            inpaint_head_feature=self.inpaint_head_feature,
            original_latent=latents_input,
        )

        # --- PRE-UNET HOOKS (ADM guidance modifies embeds here) ---
        for hook in self.hooks:
            ctx = hook.on_pre_unet(ctx)

        # Build added_cond_kwargs (use potentially modified embeds from hooks)
        added_cond_kwargs = {"text_embeds": ctx.add_text_embeds, "time_ids": ctx.add_time_ids}
        if self.ip_adapter_image is not None or self.ip_adapter_image_embeds is not None:
            added_cond_kwargs["image_embeds"] = self.image_embeds

        # Register inpaint head injection hook if enabled
        inpaint_hook_handle = None
        if self.inpaint_head_feature is not None:
            def inpaint_head_hook(module, input, output):
                return output + self.inpaint_head_feature.to(output.device, output.dtype)

            if hasattr(self.unet, 'down_blocks') and len(self.unet.down_blocks) > 0:
                first_block = self.unet.down_blocks[0]
                if hasattr(first_block, 'resnets') and len(first_block.resnets) > 0:
                    inpaint_hook_handle = first_block.resnets[0].register_forward_hook(inpaint_head_hook)

        # UNet call (the CORE operation)
        noise_pred = self.unet(
            ctx.latent_model_input, ctx.timestep,
            encoder_hidden_states=ctx.prompt_embeds,
            cross_attention_kwargs=ctx.cross_attention_kwargs,
            added_cond_kwargs=added_cond_kwargs,
            down_block_additional_residuals=ctx.down_block_res_samples,
            mid_block_additional_residual=ctx.mid_block_res_sample,
            return_dict=False,
        )[0]

        if inpaint_hook_handle is not None:
            inpaint_hook_handle.remove()

        # Free ControlNet residuals
        if ctx.down_block_res_samples is not None:
            del ctx.down_block_res_samples
        if ctx.mid_block_res_sample is not None:
            del ctx.mid_block_res_sample

        ctx.noise_pred = noise_pred

        # --- POST-UNET HOOKS (SAG, Sharpness operate here) ---
        for hook in self.hooks:
            ctx = hook.on_post_unet(ctx)
        noise_pred = ctx.noise_pred

        # CFG (Classifier-Free Guidance)
        if self.do_classifier_free_guidance:
            uncond, text = noise_pred.chunk(2)

            if _log_step:
                # Deep diagnostic: log uncond and cond predictions separately
                # This isolates whether the UNet itself produces abnormal magnitudes
                # or if the issue is in CFG amplification
                diff = text - uncond
                logger.debug(f"[STEP {self.current_step}/{self.num_inference_steps}] "
                             f"UNet uncond: std={uncond.std():.4f}, "
                             f"UNet cond: std={text.std():.4f}, "
                             f"diff (cond-uncond): std={diff.std():.4f}, "
                             f"CFG scale={self.guidance_scale}")

            noise_pred = uncond + self.guidance_scale * (text - uncond)
            if self.guidance_rescale > 0.0:
                noise_pred = rescale_noise_cfg(noise_pred, text, guidance_rescale=self.guidance_rescale)

        if _log_step:
            logger.debug(f"[STEP {self.current_step}/{self.num_inference_steps}] CFG output: "
                         f"min={noise_pred.min():.4f}, max={noise_pred.max():.4f}, "
                         f"std={noise_pred.std():.4f}")

        ctx.noise_pred = noise_pred

        # --- POST-CFG HOOKS ---
        for hook in self.hooks:
            ctx = hook.on_post_cfg(ctx)

        self.current_step += 1
        return ctx.noise_pred

    def cleanup(self):
        """Clean up ControlNet-related tensors to free VRAM."""
        if self.controlnet is not None:
            if self.controlnet_on_gpu:
                self.controlnet.to('cpu')
                self.controlnet_on_gpu = False

            if self.control_image is not None:
                if isinstance(self.control_image, list):
                    for img in self.control_image:
                        del img
                del self.control_image
                self.control_image = None

            clear_gpu_memory()
