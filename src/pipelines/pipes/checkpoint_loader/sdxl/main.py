from pathlib import Path
from typing import Dict, Any, List

import torch

from src.pipelines.outputs import ModelGenerationOutput
from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_clip import SDXLClipTextEncoder
from src.pipelines.contracts import logger
from src.pipelines.contracts import PipeInput, IOType, PipeInputSpec, PipeOutputSpec, PipeConfigSpec
from src.pipelines.models import BaseModel
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes.checkpoint_loader.sdxl.sdxl_model import SDXLModel


class _SdxlResidencyHandle:
    """Evictable handle registering a GPU-resident SDXL pipe with the native
    engine's ``GpuResidencyManager``.

    Closes the cross-engine OOM blind spot: a native generation that runs after an
    SDXL one (or vice-versa) can now evict SDXL's still-resident diffusers pipe to
    CPU to make VRAM, instead of OOMing on it. ``offload()`` is what the manager
    calls — it moves the pipe to CPU and de-registers; the next SDXL acquire
    (``_register_with_residency``) re-homes it to the GPU and re-registers.

    Only registered for pipes that are *fully* GPU-resident. A pipe using
    diffusers' own sequential/model CPU offload already frees its own VRAM per
    step, and manually ``.to()``-ing it would fight those hooks — so it is left
    unregistered.
    """

    def __init__(self, model: "SDXLModel", device: str, size_gb: float) -> None:
        self.model = model
        self.device = str(device)
        self.size_gb = size_gb
        self.offloaded = False

    def offload(self) -> None:
        pipe = getattr(self.model, "pipe", None)
        if pipe is not None:
            try:
                pipe.to("cpu")
            except Exception:  # pragma: no cover - best-effort eviction
                logger.debug("[CHECKPOINT LOADER SDXL] residency offload to cpu failed", exc_info=True)
        self.offloaded = True
        try:
            from src.platform.runtime.native.memory.residency import get_residency_manager
            get_residency_manager().note_offloaded(self)
        except Exception:  # pragma: no cover - native engine may be absent
            logger.debug("[CHECKPOINT LOADER SDXL] note_offloaded failed", exc_info=True)


def _estimate_pipe_gb(pipe) -> float:
    """Rough resident size (GB) of the SDXL pipe's parameterised components."""
    total = 0
    for name in ("unet", "vae", "text_encoder", "text_encoder_2"):
        comp = getattr(pipe, name, None)
        if isinstance(comp, torch.nn.Module):
            total += sum(p.numel() * p.element_size() for p in comp.parameters())
    return total / (1024 ** 3)


def _pipe_is_fully_resident(pipe) -> bool:
    """True when the pipe's UNet weights live on CUDA (no diffusers CPU offload)."""
    unet = getattr(pipe, "unet", None)
    if not isinstance(unet, torch.nn.Module):
        return False
    try:
        return next(unet.parameters()).device.type == "cuda"
    except StopIteration:  # pragma: no cover - unet with no params
        return False


class CheckpointLoaderSDXLPipe(BaseModelLoaderPipe):
    name = "checkpoint_loader"
    description = "Specialized pipe for loading SDXL models with optimized settings"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "loras": [],
            "mode": "txt2img",
            "device": "cuda",
            "dtype": "float16",
            "nsfw": False,
            "clip_skip": 2,
            "vram_limit_gb": None,  # GPU VRAM limit in GB (None = auto-detect)
            "extras": {}
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        """Return specification of configuration parameters this pipe accepts"""
        return [
            PipeConfigSpec("model", dict, None, "Model template configuration", required=True),
            PipeConfigSpec("loras", list, [], "List of LoRA configurations", required=False),
            PipeConfigSpec("mode", str, "txt2img", "Generation mode (txt2img, img2img, etc.)", required=False,
                          choices=["txt2img", "img2img"]),
            PipeConfigSpec("device", str, "cuda", "Device to load model on", required=False,
                          choices=["cuda", "cpu", "mps"]),
            PipeConfigSpec("dtype", str, "float16", "Data type for model weights", required=False,
                          choices=["float16", "float32", "bfloat16"]),
            PipeConfigSpec("nsfw", bool, False, "Enable NSFW content filtering", required=False),
            PipeConfigSpec("clip_skip", int, None, "Number of CLIP layers to skip (None for default SDXL behavior)", required=False,
                          min_value=0, max_value=12),
            PipeConfigSpec("vram_limit_gb", float, None, "GPU VRAM limit in GB (None = auto-detect based on hardware)",
                          required=False, min_value=4.0, max_value=128.0),
            PipeConfigSpec("extras", dict, {}, "Extra configuration parameters", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        """CheckpointLoader uses the MODELS lifecycle service for caching, plus memory management services"""
        return [
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager service for VRAM monitoring", is_array=False),
            PipeInputSpec("MEMORY", IOType.SERVICE, False, "Memory manager service for intelligent allocation", is_array=False),
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for cross-generation model reuse", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        """CheckpointLoader produces model, text_encoder, and vae outputs"""
        return [
            PipeOutputSpec("model", IOType.MODEL, "Loaded SDXL model", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "CLIP text encoder for prompt processing", is_array=False),
            PipeOutputSpec("vae", IOType.VAE, "VAE for image encoding/decoding", is_array=False),
        ]

    def validate(self) -> None:
        model_template = self.config.get("model")
        model_base = BaseModel(model_template['base'])
        if model_base != BaseModel.SDXL:
            raise ValueError(f"This pipe variant is for SDXL models only, got {model_base}")

    def progress_message(self) -> str:
        model_path = self.config.get("model")['file_path']
        return f"Loading SDXL model <<MODEL:{Path(model_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        model_template = self.config.get("model")
        models_output = [ModelGenerationOutput(name=model_template['name'], type="checkpoint")]
        for lora in self.config.get("loras", []):
            if lora['weight'] == '' or lora['weight'] is None or float(lora['weight']) == 0:
                continue
            lora_name = lora['name'] if lora.get('name') else Path(lora['file_path']).stem
            models_output.append(ModelGenerationOutput(name=lora_name, type="lora", weight=lora['weight']))
        return models_output

    def cache_key(self) -> str:
        return "checkpoint_loader/sdxl"

    def fingerprint(self) -> str:
        model_path = self.config.get("model")['file_path']
        desired_loras = {}
        for lora in self.config.get("loras", []):
            if lora['weight'] == '' or lora['weight'] is None or float(lora['weight']) == 0:
                continue
            desired_loras[Path(lora['file_path'])] = lora['weight']
        # Fingerprint is model path + active LoRAs, so a cache hit requires
        # both to match exactly.
        return f"{model_path}|{sorted(desired_loras.items(), key=lambda kv: str(kv[0]))}"

    def _resolve_vram_limit_gb(self, pipe_input: PipeInput) -> float:
        gpu_manager = pipe_input.input.get("GPU", None)
        memory_manager = pipe_input.input.get("MEMORY", None)

        vram_limit_gb = self.config.get("vram_limit_gb", None)
        if gpu_manager and vram_limit_gb is None:
            # No cap configured on the backend - bound only by available hardware
            vram_limit_gb = gpu_manager.get_vram_budget()
            logger.info(f"[CHECKPOINT LOADER SDXL] Using dynamic VRAM budget: {vram_limit_gb:.2f}GB")
        elif vram_limit_gb is not None:
            logger.info(f"[CHECKPOINT LOADER SDXL] Using configured VRAM limit: {vram_limit_gb}GB")
        else:
            # No service and no config - use conservative default
            vram_limit_gb = 8.0
            logger.info(f"[CHECKPOINT LOADER SDXL] No VRAM limit specified, using default: {vram_limit_gb}GB")

        # Log memory recommendation if available
        if memory_manager:
            memory_manager.log_memory_recommendation(
                image_size=(1024, 1024),  # Default SDXL resolution for estimation
                model_type="sdxl",
                context="checkpoint_loader"
            )

        return vram_limit_gb

    def load_model(self, pipe_input: PipeInput) -> SDXLModel:
        model_template = self.config.get("model")
        model_path = model_template['file_path']
        vram_limit_gb = self._resolve_vram_limit_gb(pipe_input)

        logger.info("[CHECKPOINT LOADER SDXL] Loading new SDXL model with optimized settings")
        loras = [
            {"file_path": Path(lora['file_path']), "weight": lora['weight']}
            for lora in self.config.get("loras", [])
        ]
        new_model = SDXLModel(
            template=model_template,
            config={
                "path": model_path,
                "device": self.config.get("device", "cuda"),
                "dtype": self.config.get("dtype", "float16"),
                "nsfw": self.config.get("nsfw", False),
                "loras": loras,
                "extras": self.config.get("extras", {}),
                "vram_limit_gb": vram_limit_gb,
            }
        )
        new_model.load(mode=self.config.get("mode", "txt2img"))
        return new_model

    def after_acquire(self, model: SDXLModel, pipe_input: PipeInput, fingerprint: str) -> None:
        # load() is idempotent (returns immediately if pipe already built with
        # the same config), so calling it again on a cache hit is cheap and
        # applies the requested mode (txt2img/img2img) to the reused model.
        model.load(mode=self.config.get("mode", "txt2img"))
        if hasattr(model, "clear_hooks"):
            model.clear_hooks()
        self._register_with_residency(model)

    def _register_with_residency(self, model: SDXLModel) -> None:
        """Register the GPU-resident SDXL pipe with the native GpuResidencyManager.

        Runs on every acquire (hit or miss). On a hit where a prior native
        generation evicted our pipe to CPU (``handle.offloaded``), re-home it to the
        GPU first, then re-register — so SDXL keeps working after being evicted.
        A pipe using diffusers' own CPU offload is never registered (it manages its
        own VRAM and manual moves would fight its hooks). No-op without CUDA.
        """
        device = self.config.get("device", "cuda")
        if not torch.cuda.is_available() or not str(device).startswith("cuda"):
            return
        pipe = getattr(model, "pipe", None)
        if pipe is None:
            return
        try:
            from src.platform.runtime.native.memory.residency import get_residency_manager
        except Exception:  # pragma: no cover - native engine unavailable
            return
        handle = getattr(model, "_residency_handle", None)

        if handle is not None and handle.offloaded:
            # We (native eviction) moved it to CPU earlier — bring it back.
            try:
                pipe.to(device)
            except Exception:  # pragma: no cover - best-effort re-home
                logger.debug("[CHECKPOINT LOADER SDXL] re-home to GPU failed", exc_info=True)
        elif not _pipe_is_fully_resident(pipe):
            # First registration only for fully-resident pipes; diffusers-offloaded
            # pipes manage their own VRAM.
            return

        if handle is None:
            handle = _SdxlResidencyHandle(model, device, _estimate_pipe_gb(pipe))
            model._residency_handle = handle
        handle.offloaded = False
        get_residency_manager().note_resident(handle, device, handle.size_gb)

    def build_output(self, model: SDXLModel, pipe_input: PipeInput, fingerprint: str) -> Dict[str, Any]:
        model_base = BaseModel(self.config.get("model")['base'])

        # Get clip_skip from config, default to None for standard SDXL behavior
        clip_skip_value = self.config.get("clip_skip", None)
        # Handle string values from YAML
        if clip_skip_value is not None:
            if isinstance(clip_skip_value, str):
                clip_skip = int(clip_skip_value) if clip_skip_value else None
            else:
                clip_skip = clip_skip_value
        else:
            clip_skip = None
        logger.debug(f"[CHECKPOINT LOADER SDXL] Using clip_skip={clip_skip} (from config value: {repr(clip_skip_value)})")

        # The CLIP wrapper is a cheap view over the (possibly cached) model's
        # own encoders/tokenizers, so it is rebuilt on every call rather than
        # cached separately. `_model_fingerprint` lets downstream pipes
        # (prompt_encoder) key their own conditioning cache off model identity.
        clip = SDXLClipTextEncoder(
            pipe=model.pipe,
            text_encoder=model.pipe.text_encoder,
            text_encoder_2=model.pipe.text_encoder_2,
            tokenizer=model.pipe.tokenizer,
            tokenizer_2=model.pipe.tokenizer_2,
            device=self.config.get("device", "cuda"),
            clip_skip=clip_skip,
            base_model=model_base,
        )
        clip._model_fingerprint = fingerprint

        return {
            "model": model,
            "text_encoder": clip,
            "vae": model.pipe.vae,
        }
