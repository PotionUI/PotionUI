"""Model loader for the native MiniMax-Music3 text-to-music family.

Every component ships as its own standalone Comfy-Org single-file repack
(port plan S6 layout): the flow-matching DiT (with its fused condition
encoder), the fused text encoder (global LLM + depth decoder + tokenizer),
and the DAV vocoder. All three are ALWAYS loaded -- unlike LTX's opt-in
`audio: true`, this family has no mode that skips any of them (text-to-music
only, no LoRA in v1, per the port plan's phase-1 scope).

The text encoder does NOT go through `NativeEngineLoader`/`text_encoders/
loader.py` (see `te_loader.py`'s module docstring for why) -- it is acquired
through its own loader closure instead, but the SAME `MODELS.acquire()`
per-component-reuse idiom every other component here uses.

Unlike MiniMax-H3's TE, the LM is acquired EAGERLY (not behind a lazy
`te_factory`): Music3 has no separate `prompt_encoder` stage whose own
conditioning cache could skip touching it -- the AR loop that consumes it
lives inside THIS family's generator pipe and runs on every generation
regardless, so there is no cache-hit path that would make a lazy factory pay
off the way H3's does.
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
    path_of as _path_of,
    vram_budget as _vram_budget_fn,
)
from src.pipelines.pipes.model_loader.minimax_music3.bundle import MiniMaxMusic3ModelBundle
from src.pipelines.pipes.model_loader.minimax_music3.te_loader import load_minimax_music3_te


class ModelLoaderMinimaxMusic3Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native MiniMax-Music3 checkpoint set (DiT + fused text encoder + DAV vocoder)"

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "model": None,
            "text_encoder": None,
            "vae": None,
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("model", dict, None, "MiniMax-Music3 flow-matching DiT checkpoint", required=True),
            PipeConfigSpec("text_encoder", dict, None,
                           "MiniMax-Music3 fused text encoder (global LLM + depth decoder + tokenizer)",
                           required=True),
            PipeConfigSpec("vae", dict, None, "MiniMax-Music3 DAV vocoder", required=True),
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
            PipeOutputSpec("model", IOType.MODEL, "MiniMax-Music3 model bundle (DiT + text encoder + DAV)", is_array=False),
        ]

    def progress_message(self) -> str:
        model_path = _path_of(self.config.get("model")) or "?"
        return f"Loading MiniMax-Music3 model <<MODEL:{Path(model_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (
            ("model", "minimax_music3_dit"),
            ("text_encoder", "minimax_music3_te"),
            ("vae", "minimax_music3_dav"),
        ):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        return out

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        model_path = _path_of(self.config.get("model"))
        te_path = _path_of(self.config.get("text_encoder"))
        vae_path = _path_of(self.config.get("vae"))
        if not (model_path and te_path and vae_path):
            raise ValueError(
                "model_loader/minimax_music3 requires model, text_encoder and vae file paths"
            )

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")
        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        models = pipe_input.input.get("MODELS", None)

        def acquire(key: str, fp: str, kind: str, path: str, **kwargs: Any) -> NativeModel:
            # Every Music3 component lives in its OWN standalone file (same
            # posture as MiniMax-H3), so every acquire estimates from its
            # own file size -- no slice-before-estimate special-casing.
            estimated_vram_gb = file_size_gb(path)
            if models is not None:
                return models.acquire(
                    key=key, fingerprint=fp, loader=lambda: loader.load(path, kind, **kwargs),
                    estimated_vram_gb=estimated_vram_gb,
                )
            return loader.load(path, kind, **kwargs)

        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=3)
        progress.advance("DiT", f"native/dit/{model_path}")
        dit_model = acquire(f"native/dit/{model_path}", f"{model_path}|{dtype}", "diffusion_model", model_path)
        progress.advance("audio VAE", f"native/audio_vae/{vae_path}")
        vae_model = acquire(f"native/audio_vae/{vae_path}", f"{vae_path}|{dtype}", "audio_vae", vae_path)

        lm_cache_key = f"native/te/{te_path}"

        def load_lm() -> NativeModel:
            return load_minimax_music3_te(te_path, device=device)

        progress.advance("text encoder", lm_cache_key)
        if models is not None:
            lm_model = models.acquire(
                key=lm_cache_key, fingerprint=f"{te_path}|{dtype}", loader=load_lm,
                estimated_vram_gb=file_size_gb(te_path),
            )
        else:
            lm_model = load_lm()

        bundle = MiniMaxMusic3ModelBundle(
            dit=dit_model, lm=lm_model, dav=vae_model, lm_cache_key=lm_cache_key,
        )
        return PipeOutput(output={"model": bundle})

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER MINIMAX-MUSIC3")
