"""Model loader for the native Qwen-Image family (txt2img).

Qwen-Image is a single-checkpoint MMDiT plus ONE text encoder (Qwen2.5-VL, no
CLIP-L) and the Wan-2.1 causal-3D VAE. The native engine detects the DiT variant
from the checkpoint, so this pipe just supplies the three component files.

Like the Flux/Wan loaders, each heavy component (text encoder, VAE, DiT) is
acquired under its OWN ``MODELS`` cache key: a shared Qwen2.5-VL / VAE is reused
across Qwen presets, and a LoRA change re-acquires ONLY the DiT (LoRAs are in the
DiT fingerprint, not the TE/VAE ones).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.engine import NativeEngineLoader, NativeModel
from src.pipelines.contracts import (
    IOType,
    PipeInput,
    PipeInputSpec,
    PipeOutput,
    PipeOutputSpec,
    PipeConfigSpec,
)
from src.pipelines.pipes._shared.generation.loader_base import BaseModelLoaderPipe
from src.pipelines.pipes._shared.generation.loader_helpers import (
    ComponentProgress,
    active_loras as _active_loras,
    apply_loras_to as _apply_loras_to,
    lora_stack_fingerprint as _lora_stack_fingerprint,
    path_of as _path_of,
    reemit_lora_application_diagnostics as _emit_lora_diagnostics,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes._shared.generation.loader_lifecycle import (
    Component,
    ComponentLifecycle,
)
from src.pipelines.pipes.model_loader.qwen.bundle import QwenModelBundle
from src.pipelines.pipes.model_loader.qwen.qwen_clip import QwenClipTextEncoder


class ModelLoaderQwenPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Qwen-Image checkpoint set (MMDiT + Qwen2.5-VL TE + causal-3D VAE)"

    # -- declaration -------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "diffusion_model": None,
            "text_encoder": None,
            "vae": None,
            "loras": [],
            "device": "cuda",
            "dtype": "bfloat16",
            "vision": False,
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Qwen-Image MMDiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen2.5-VL text encoder (single TE, no CLIP-L)", required=True),
            PipeConfigSpec("vae", dict, None, "Qwen-Image (Wan-2.1 causal-3D) VAE", required=True),
            PipeConfigSpec("loras", list, [], "LoRA adapters (busts only the DiT cache)", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                           choices=["bfloat16", "float16", "float32"]),
            PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)", required=False),
            # Edit mode only: keep+load the Qwen2.5-VL vision tower so the prompt
            # encoder can condition on the source image, not just its text
            # description. Off by default (txt2img/img2img memory footprint
            # unaffected) -- folded into te_fp below, NOT just passed to the
            # loader, so a text-only and a vision-enabled load of the same TE
            # file never alias to the same model-lifecycle cache entry.
            PipeConfigSpec("vision", bool, False, "Load the vision tower for image-conditioned editing", required=False),
        ]

    @classmethod
    def inputs(cls) -> List[PipeInputSpec]:
        return [
            PipeInputSpec("MODELS", IOType.SERVICE, False, "Model lifecycle service for per-component reuse", is_array=False),
            PipeInputSpec("GPU", IOType.SERVICE, False, "GPU manager for the VRAM budget", is_array=False),
        ]

    @classmethod
    def outputs(cls) -> List[PipeOutputSpec]:
        return [
            PipeOutputSpec("model", IOType.MODEL, "Qwen-Image model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen2.5-VL text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks reused by our process() -----------------

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Qwen-Image model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("diffusion_model", "qwen_dit"),
            ("text_encoder", "qwen_text_encoder"),
            ("vae", "qwen_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for lora in _active_loras(self.config.get("loras")):
            out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type="lora", weight=lora["weight"]))
        return out

    # -- multi-component load ----------------------------------------------

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Emit progress/models, then acquire TE / VAE / DiT independently.

        Overrides the single-acquire base flow because Qwen caches each heavy
        component under its own key (shared TE/VAE reuse; LoRA busts only the DiT).
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError("model_loader/qwen requires diffusion_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))
        vision = bool(self.config.get("vision", False))

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        te_fp = f"{te_path}|{dtype}|vision={vision}"
        vae_fp = f"{vae_path}|{dtype}"
        lora_fp = _lora_stack_fingerprint(loras) or "none"
        dit_fp = f"{dit_path}|{dtype}|{lora_fp}"

        def load_te() -> NativeModel:
            return loader.load(te_path, "text_encoder", vision=vision)

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            model = loader.load(dit_path, "diffusion_model")
            model._active_lora_application = self._apply_loras(model, loras)  # noqa: SLF001
            return model

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        lifecycle = ComponentLifecycle(models, progress)

        te_key = f"native/te/{te_path}"
        te = Component("text encoder", te_key, te_fp, load_te, file_size_gb(te_path))
        vae = Component("VAE", f"native/vae/{vae_path}", vae_fp, load_vae, file_size_gb(vae_path))
        dit = Component("DiT", f"native/dit/{dit_path}", dit_fp, load_dit, file_size_gb(dit_path))

        if not lifecycle.caching:
            # Nothing would hold a deferred encoder between the thunk
            # returning and the first encode, so load everything up front.
            te_model = lifecycle.acquire(te)
            vae_model = lifecycle.acquire(vae)
            dit_model = lifecycle.acquire(dit)
            _emit_lora_diagnostics(dit_model, generation_outputs, "MODEL LOADER QWEN")
            return PipeOutput(output={
                "model": QwenModelBundle(dit=dit_model, te=te_model, vae=vae_model, te_cache_key=te_key),
                "text_encoder": QwenClipTextEncoder(
                    te_model.module, device=device, model_fingerprint=f"{te_fp}|{dit_fp}",
                ),
            })

        # The TE is acquired at most once, by `QwenClipTextEncoder.encoder`,
        # the first time `prompt_encoder` actually misses the prompt-embed
        # cache. See model_loader/krea2 for the full rationale.
        vae_model = lifecycle.acquire(vae)
        dit_model = lifecycle.acquire(dit)
        _emit_lora_diagnostics(dit_model, generation_outputs, "MODEL LOADER QWEN")
        bundle = QwenModelBundle(dit=dit_model, te=None, vae=vae_model, te_cache_key=te_key)
        clip = QwenClipTextEncoder(
            device=device, model_fingerprint=f"{te_fp}|{dit_fp}",
            te_loader=lifecycle.deferred_module(te),
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    # -- helpers -----------------------------------------------------------

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER QWEN")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]):
        return _apply_loras_to(dit_model, loras, "MODEL LOADER QWEN")
