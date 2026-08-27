"""Model loader for the native Z-Image family (txt2img).

Z-Image is a single-checkpoint NextDiT (Lumina-Image-2.0 backbone at dim 3840)
plus ONE text encoder (Qwen3-4B, no CLIP-L) and the Flux-style 2D AE. The native
engine detects the DiT variant from the checkpoint, so this pipe just supplies
the three component files.

Like the Qwen loader, each heavy component (text encoder, VAE, DiT) is acquired
under its OWN ``MODELS`` cache key: a shared Qwen3 / VAE is reused across presets,
and a LoRA change re-acquires ONLY the DiT. The text encoder is loaded via
``load_text_encoder(..., te_variant="zimage")`` directly (not the generic
``NativeEngineLoader.load`` TE path) because Z-Image's Qwen3-4B is structurally
identical to Klein's but consumes a different hidden state (penultimate layer),
so the encode contract must be selected by this caller.
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
from src.platform.runtime.native.text_encoders.loader import load_text_encoder
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
from src.pipelines.pipes.model_loader.z_image.bundle import ZImageModelBundle
from src.pipelines.pipes.model_loader.z_image.z_image_clip import ZImageClipTextEncoder


class ModelLoaderZImagePipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Z-Image checkpoint set (NextDiT + Qwen3-4B TE + 2D AE)"

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
            PipeConfigSpec("diffusion_model", dict, None, "Z-Image NextDiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None, "Qwen3-4B text encoder (single TE, no CLIP-L)", required=True),
            PipeConfigSpec("vae", dict, None, "Z-Image (Flux-style 2D) VAE", required=True),
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
            PipeOutputSpec("model", IOType.MODEL, "Z-Image model bundle (DiT + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Qwen3-4B text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks reused by our process() -----------------

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading Z-Image model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("diffusion_model", "z_image_dit"),
            ("text_encoder", "z_image_text_encoder"),
            ("vae", "z_image_vae"),
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

        Overrides the single-acquire base flow because Z-Image caches each heavy
        component under its own key (shared TE/VAE reuse; LoRA busts only the DiT).
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (dit_path and te_path and vae_path):
            raise ValueError("model_loader/z_image requires diffusion_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        loras = _active_loras(self.config.get("loras"))

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        te_fp = f"{te_path}|{dtype}|zimage"
        vae_fp = f"{vae_path}|{dtype}"
        lora_fp = "+".join(f"{l['file_path']}@{l['weight']}" for l in loras) or "none"
        dit_fp = f"{dit_path}|{dtype}|{lora_fp}"

        def load_te() -> NativeModel:
            # Z-Image's encode contract (penultimate layer + Z-Image template)
            # is selected here — the file is structurally identical to Klein's TE.
            encoder = load_text_encoder(te_path, device="cpu", te_variant="zimage")
            return NativeModel("text_encoder", encoder)

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            model = loader.load(dit_path, "diffusion_model")
            self._apply_loras(model, loras)
            return model

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        if models is not None:
            progress.advance("text encoder", f"native/te/{te_path}|zimage")
            te_model = models.acquire(key=f"native/te/{te_path}|zimage", fingerprint=te_fp, loader=load_te, estimated_vram_gb=file_size_gb(te_path))
            progress.advance("VAE", f"native/vae/{vae_path}")
            vae_model = models.acquire(key=f"native/vae/{vae_path}", fingerprint=vae_fp, loader=load_vae, estimated_vram_gb=file_size_gb(vae_path))
            progress.advance("DiT", f"native/dit/{dit_path}")
            dit_model = models.acquire(key=f"native/dit/{dit_path}", fingerprint=dit_fp, loader=load_dit, estimated_vram_gb=file_size_gb(dit_path))
        else:
            progress.advance("text encoder", f"native/te/{te_path}|zimage")
            progress.advance("VAE", f"native/vae/{vae_path}")
            progress.advance("DiT", f"native/dit/{dit_path}")
            te_model, vae_model, dit_model = load_te(), load_vae(), load_dit()

        bundle = ZImageModelBundle(dit=dit_model, te=te_model, vae=vae_model, te_cache_key=f"native/te/{te_path}|zimage")
        clip = ZImageClipTextEncoder(
            te_model.module, device=device, model_fingerprint=f"{te_fp}|{dit_fp}"
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    # -- helpers -----------------------------------------------------------

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER Z-IMAGE")

    @staticmethod
    def _apply_loras(dit_model: NativeModel, loras: List[Dict[str, Any]]) -> None:
        _apply_loras_to(dit_model, loras, "MODEL LOADER Z-IMAGE")
