"""Model loader for the native Wan 2.1 / 2.2 video family.

Wan 2.2 14B is a HIGH/LOW-noise expert PAIR — this pipe acquires both DiT files
(plus one UMT5 text encoder and the causal-3D VAE), each under its own MODELS
cache key so a shared UMT5/VAE is reused across Wan presets. A single-DiT Wan
(2.1 or the 5B ti2v) just leaves the low-noise picker empty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
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
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.wan22.acquire import acquire_wan_dit
from src.pipelines.pipes.model_loader.wan22.bundle import WanModelBundle
from src.pipelines.pipes.model_loader.wan22.wan_clip import WanClipTextEncoder


class ModelLoaderWan22Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native Wan video checkpoint set (high/low DiT + UMT5 + causal-3D VAE)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "high_noise_model": None,
            "low_noise_model": None,
            "text_encoder": None,
            "vae": None,
            "loras_high": [],
            "loras_low": [],
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("high_noise_model", dict, None, "Wan DiT (high-noise expert, or the only DiT)", required=True),
            PipeConfigSpec("low_noise_model", dict, None, "Wan low-noise expert (2.2 14B dual-expert only)", required=False),
            PipeConfigSpec("text_encoder", dict, None, "UMT5-XXL text encoder", required=True),
            PipeConfigSpec("vae", dict, None, "Wan causal-3D VAE", required=True),
            PipeConfigSpec("loras_high", list, [], "LoRAs for the high-noise expert (or the only DiT); busts only that DiT's cache", required=False),
            PipeConfigSpec("loras_low", list, [], "LoRAs for the low-noise expert (Wan 2.2 pairs ship separate high/low files)", required=False),
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
            PipeOutputSpec("model", IOType.MODEL, "Wan model bundle (DiT(s) + TE + VAE)", is_array=False),
            PipeOutputSpec("text_encoder", IOType.TEXT_ENCODER, "Wan UMT5 text encoder (ClipTextEncoder ABC)", is_array=False),
        ]

    def progress_message(self) -> str:
        high = _path_of(self.config.get("high_noise_model")) or "?"
        return f"Loading Wan model <<MODEL:{Path(high).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("high_noise_model", "wan_dit_high"),
            ("low_noise_model", "wan_dit_low"),
            ("text_encoder", "wan_umt5"),
            ("vae", "wan_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        for key, label in (("loras_high", "lora_high"), ("loras_low", "lora_low")):
            for lora in _active_loras(self.config.get(key)):
                out.append(ModelGenerationOutput(name=Path(lora["file_path"]).stem, type=label, weight=lora["weight"]))
        return out

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        high_path = _path_of(self.config.get("high_noise_model"))
        low_path = _path_of(self.config.get("low_noise_model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (high_path and te_path and vae_path):
            raise ValueError("model_loader/wan22 requires high_noise_model, text_encoder and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        # Wan 2.2 LoRAs ship as high/low PAIRS — each file targets one expert
        # (ComfyUI runs two separate LoraLoader chains). Never cross-apply.
        loras_high = _active_loras(self.config.get("loras_high"))
        loras_low = _active_loras(self.config.get("loras_low"))
        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(
            generation_outputs, models, self.progress_message(), total=4 if low_path else 3,
        )

        def acquire(key: str, fp: str, kind: str, path: str) -> NativeModel:
            if models is not None:
                return models.acquire(key=key, fingerprint=fp, loader=lambda: loader.load(path, kind))
            return loader.load(path, kind)

        progress.advance("high-noise DiT" if low_path else "DiT", f"native/dit/{high_path}")
        high_dit = acquire_wan_dit(models, loader, high_path, dtype, loras_high, log_tag="MODEL LOADER WAN")
        low_dit = None
        if low_path:
            progress.advance("low-noise DiT", f"native/dit/{low_path}")
            low_dit = acquire_wan_dit(models, loader, low_path, dtype, loras_low, log_tag="MODEL LOADER WAN")
        progress.advance("text encoder", f"native/te/{te_path}")
        te_model = acquire(f"native/te/{te_path}", f"{te_path}|{dtype}", "text_encoder", te_path)
        progress.advance("VAE", f"native/vae/{vae_path}")
        vae_model = acquire(f"native/vae/{vae_path}", f"{vae_path}|{dtype}", "vae", vae_path)

        bundle = WanModelBundle(
            high_dit=high_dit, te=te_model, vae=vae_model, low_dit=low_dit,
            loras_high=loras_high, loras_low=loras_low,
        )
        clip = WanClipTextEncoder(
            te_model.module, device=device, model_fingerprint=f"{te_path}|{high_path}|{low_path or ''}"
        )
        return PipeOutput(output={"model": bundle, "text_encoder": clip})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER WAN")
