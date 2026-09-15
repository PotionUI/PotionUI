"""Model loader for the native YuE2-3B text-to-music family."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
from src.platform.runtime.model_lifecycle.lifecycle import file_size_gb
from src.platform.runtime.native.arch.yue2.tokenizer import YuE2Tokenizer
from src.platform.runtime.native.engine import NativeEngineLoader
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
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes._shared.generation.loader_lifecycle import (
    Component,
    ComponentLifecycle,
)
from src.pipelines.pipes.model_loader.yue2.bundle import YuE2ModelBundle

_EXPECTED_LATENT_DIM = 64


class ModelLoaderYuE2Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native YuE2-3B checkpoint set (AR/NAR backbone + Oobleck VAE decoder)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "vae": None,
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None, "YuE2-3B AR/NAR backbone checkpoint", required=True),
            PipeConfigSpec("vae", dict, None, "YuE2 Oobleck VAE decoder checkpoint", required=True),
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
            PipeOutputSpec("model", IOType.MODEL, "YuE2-3B model bundle (AR/NAR backbone + VAE)", is_array=False),
        ]

    def progress_message(self) -> str:
        model_path = _path_of(self.config.get("model")) or "?"
        return f"Loading YuE2-3B model <<MODEL:{Path(model_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("model", "yue2_lm"),
            ("vae", "yue2_vae"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        return out

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        model_path = _path_of(self.config.get("model"))
        vae_path = _path_of(self.config.get("vae"))
        if not (model_path and vae_path):
            raise ValueError("model_loader/yue2 requires model and vae file paths")

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=2)
        lifecycle = ComponentLifecycle(models, progress)

        def _component(label: str, key: str, kind: str, path: str) -> Component:
            return Component(
                label, key, f"{path}|{dtype}", lambda: loader.load(path, kind), file_size_gb(path),
            )

        lm_cache_key = f"native/dit/{model_path}"
        lm_model = lifecycle.acquire(_component("AR/NAR backbone", lm_cache_key, "diffusion_model", model_path))
        latent_dim = lm_model.module.cfg.latent_dim
        if latent_dim != _EXPECTED_LATENT_DIM:
            raise ValueError(
                f"model_loader/yue2: '{model_path}' has latent_dim={latent_dim}, expected "
                f"{_EXPECTED_LATENT_DIM} -- the NAR acoustic stage hardcodes {_EXPECTED_LATENT_DIM}"
            )
        if getattr(lm_model, "tokenizer", None) is None:
            lm_model.tokenizer = YuE2Tokenizer()

        vae_model = lifecycle.acquire(
            _component("VAE decoder", f"native/audio_vae/{vae_path}", "audio_vae", vae_path)
        )

        bundle = YuE2ModelBundle(lm=lm_model, vae=vae_model, lm_cache_key=lm_cache_key)
        return PipeOutput(output={"model": bundle})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER YUE2")
