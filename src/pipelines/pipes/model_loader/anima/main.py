"""Model loader for the native Anima family (txt2img).

Anima is a single-checkpoint MiniTrainDIT (with an in-model LLMAdapter) plus ONE
text encoder (Qwen3-0.6B) and the Wan-2.1 causal-3D VAE (16ch, shared with
Qwen-Image / Krea-2). The native engine detects the DiT variant from the
checkpoint, so this pipe just supplies the three component files.

Like the Qwen loader, each heavy component (text encoder, VAE, DiT) is acquired
under its OWN ``MODELS`` cache key: a shared Qwen3-0.6B / VAE is reused across
Anima presets, and a LoRA change re-acquires ONLY the DiT (LoRAs are in the DiT
fingerprint, not the TE/VAE ones).
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
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.anima.anima_clip import AnimaClipTextEncoder
from src.pipelines.pipes.model_loader.anima.bundle import AnimaModelBundle


class ModelLoaderAnimaPipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Anima checkpoint set (MiniTrainDIT + Qwen3-0.6B TE + causal-3D VAE)"

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
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "Anima MiniTrainDIT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-0.6B text encoder (single TE, no CLIP-L)", required=True),
            PipeConfigSpec("vae", dict, None, "Anima (Wan-2.1 causal-3D) VAE", required=True),
            PipeConfigSpec("loras", list, [], "LoRA adapters (busts only the DiT cache)", required=False),
            PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"]),
            PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                           choices=["bfloat16", "float16", "float32"]),
            PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)", required=False),
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
            PipeOutputSpec("model", IOType.MODEL, "Anima model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen3-0.6B text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks reused by our process() -----------------

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Anima model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("diffusion_model", "anima_dit"),
            ("text_encoder", "anima_text_encoder"),
            ("vae", "anima_vae"),
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

        Overrides the single-acquire base flow because Anima caches each heavy
        component under its own key (shared TE/VAE reuse; LoRA busts only the DiT).
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError("model_loader/anima requires diffusion_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        te_fp = f"{te_path}|{dtype}"
        vae_fp = f"{vae_path}|{dtype}"
        lora_fp = "+".join(f"{l['file_path']}@{l['weight']}" for l in loras) or "none"
        dit_fp = f"{dit_path}|{dtype}|{lora_fp}"

        def load_te() -> NativeModel:
            return loader.load(te_path, "text_encoder")

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            model = loader.load(dit_path, "diffusion_model")
            self._apply_loras(model, loras)
            return model

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        if models is not None:
            progress.advance("text encoder", f"native/te/{te_path}")
            te_model = models.acquire(key=f"native/te/{te_path}", fingerprint=te_fp, loader=load_te, estimated_vram_gb=file_size_gb(te_path))
            progress.advance("VAE", f"native/vae/{vae_path}")
            vae_model = models.acquire(key=f"native/vae/{vae_path}", fingerprint=vae_fp, loader=load_vae, estimated_vram_gb=file_size_gb(vae_path))
            progress.advance("DiT", f"native/dit/{dit_path}")
            dit_model = models.acquire(key=f"native/dit/{dit_path}", fingerprint=dit_fp, loader=load_dit, estimated_vram_gb=file_size_gb(dit_path))
        else:
            progress.advance("text encoder", f"native/te/{te_path}")
            progress.advance("VAE", f"native/vae/{vae_path}")
            progress.advance("DiT", f"native/dit/{dit_path}")
            te_model, vae_model, dit_model = load_te(), load_vae(), load_dit()

        bundle = AnimaModelBundle(dit=dit_model, te=te_model, vae=vae_model, te_cache_key=f"native/te/{te_path}")
        clip = AnimaClipTextEncoder(
            te_model.module, device=device, model_fingerprint=f"{te_fp}|{dit_fp}"
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    # -- helpers -----------------------------------------------------------

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER ANIMA")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, "MODEL LOADER ANIMA")
