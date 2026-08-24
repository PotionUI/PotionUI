"""Model loader for the native SeedVR2 family (image restoration / upscale).

SeedVR2 (ByteDance) is a single-checkpoint NaDiT restoration transformer plus the
self-normalizing causal-video VAE — and, uniquely among native families, NO text
encoder: it conditions on a *fixed* precomputed positive prompt embedding shipped
as a raw ``.pt`` tensor. So this pipe supplies two component files (DiT + VAE) and
loads the tiny embedding inline.

Like the Anima loader, each heavy component is acquired under its OWN ``MODELS``
cache key so a shared VAE is reused across SeedVR2 presets. The prompt embedding
is a few hundred KB — loaded directly (no ``MODELS.acquire``) but folded into the
DiT fingerprint so a swapped embedding busts the reused bundle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from src.pipelines.outputs import (
    ModelGenerationOutput,
    ModelsGenerationOutput,
)
from src.platform.runtime.model_lifecycle.manager import file_size_gb
from src.platform.runtime.native.arch.seedvr2 import load_seedvr2_prompt_embedding
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
from src.pipelines.pipes.model_loader.seedvr2.bundle import SeedVR2ModelBundle


class ModelLoaderSeedVR2Pipe(BaseModelLoaderPipe):
    name = "model_loader"
    description = "Load a native SeedVR2 checkpoint set (NaDiT + self-normalizing causal-video VAE + fixed prompt embedding)"

    # -- declaration -------------------------------------------------------

    @classmethod
    def get_default_config(cls) -> Dict[str, Any]:
        return {
            "diffusion_model": None,
            "vae": None,
            "prompt_embedding": None,
            "device": "cuda",
            "dtype": "bfloat16",
        }

    @classmethod
    def configuration(cls) -> List[PipeConfigSpec]:
        return [
            PipeConfigSpec("diffusion_model", dict, None, "SeedVR2 NaDiT checkpoint", required=True),
            PipeConfigSpec("vae", dict, None, "SeedVR2 self-normalizing causal-video VAE", required=True),
            PipeConfigSpec("prompt_embedding", dict, None,
                           "Fixed positive prompt embedding (.pt tensor); no live text encoder", required=True),
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
            PipeOutputSpec("model", IOType.MODEL, "SeedVR2 model bundle (DiT + VAE + fixed prompt embedding)", is_array=False),
        ]

    # -- BaseModelLoaderPipe hooks (informational) -------------------------

    def progress_message(self) -> str:
        dit_path = _path_of(self.config.get("diffusion_model")) or "?"
        return f"Loading SeedVR2 model <<MODEL:{Path(dit_path).stem}>>"

    def describe_models(self) -> List[ModelGenerationOutput]:
        out: List[ModelGenerationOutput] = []
        for key, mtype in (("diffusion_model", "seedvr2_dit"), ("vae", "seedvr2_vae")):
            cfg = self.config.get(key)
            if _path_of(cfg):
                out.append(ModelGenerationOutput(name=cfg.get("name") or Path(_path_of(cfg)).stem, type=mtype))
        return out

    # -- multi-component load ----------------------------------------------

    def process(self, pipe_input: PipeInput, generation_outputs: callable) -> PipeOutput:
        """Emit progress/models, then acquire the DiT + VAE independently and
        load the fixed prompt embedding inline.

        Overrides the single-acquire base flow because SeedVR2 caches its two
        heavy components under their own keys (like Anima), and there is no TE /
        LoRA to thread.
        """
        self.validate()
        generation_outputs(ModelsGenerationOutput(models=self.describe_models()))

        dit_path = _path_of(self.config.get("diffusion_model"))
        vae_path = _path_of(self.config.get("vae"))
        emb_path = _path_of(self.config.get("prompt_embedding"))
        if not (dit_path and vae_path and emb_path):
            raise ValueError(
                "model_loader/seedvr2 requires diffusion_model, vae and prompt_embedding file paths"
            )

        device = self.config.get("device", "cuda")
        dtype = self.config.get("dtype", "bfloat16")

        vram_gb = self._vram_budget(pipe_input)
        loader = NativeEngineLoader(device=device, vram_gb=vram_gb)

        # The embedding is tiny but part of the conditioning identity: fold it into
        # the DiT fingerprint so a swapped embedding re-acquires the bundle.
        vae_fp = f"{vae_path}|{dtype}"
        dit_fp = f"{dit_path}|{dtype}|emb:{emb_path}"

        def load_vae() -> NativeModel:
            return loader.load(vae_path, "vae")

        def load_dit() -> NativeModel:
            return loader.load(dit_path, "diffusion_model")

        models = pipe_input.input.get("MODELS", None)
        progress = ComponentProgress(generation_outputs, models, self.progress_message(), total=2)
        if models is not None:
            progress.advance("VAE", f"native/vae/{vae_path}")
            vae_model = models.acquire(
                key=f"native/vae/{vae_path}", fingerprint=vae_fp, loader=load_vae,
                estimated_vram_gb=file_size_gb(vae_path),
            )
            progress.advance("DiT", f"native/dit/{dit_path}")
            dit_model = models.acquire(
                key=f"native/dit/{dit_path}", fingerprint=dit_fp, loader=load_dit,
                estimated_vram_gb=file_size_gb(dit_path),
            )
        else:
            progress.advance("VAE", f"native/vae/{vae_path}")
            progress.advance("DiT", f"native/dit/{dit_path}")
            vae_model, dit_model = load_vae(), load_dit()

        prompt_embedding = load_seedvr2_prompt_embedding(emb_path)

        bundle = SeedVR2ModelBundle(dit=dit_model, vae=vae_model, prompt_embedding=prompt_embedding)
        return PipeOutput(output={"model": bundle})

    # -- helpers -----------------------------------------------------------

    def _vram_budget(self, pipe_input: PipeInput) -> Optional[float]:
        return _vram_budget_fn(pipe_input, self.config.get("vram_limit_gb", None), "MODEL LOADER SEEDVR2")
