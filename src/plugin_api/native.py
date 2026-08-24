"""Running generation against the native (in-process) engine.

A plugin that needs to drive the native engine's generator directly -
VAE-encode a reference image, sample, decode - rather than only assembling a
pipeline out of existing pipes, gets a narrow, stable slice of it here.
`NativeGenerator` itself is a large, fast-moving internal class (device
placement, VRAM streaming/eviction, quantization, causal-3D tiled decode,
...); `NativeGeneratorHandle` is the structural (duck-typed) subset of it
this module actually promises to keep stable - the generation ops a
family-specific generator pipe's `generate_one` calls once a shared
`build_context` has already constructed the real generator and handed it
through `GeneratorContext.extra["generator"]`. A plugin pipe never
constructs a `NativeGenerator` itself, and should never call anything on it
beyond this surface.

`Conditioning`, `GeneratorContext`, `ProgressEmitter` and `native_step_hooks`
are the small, already-stable pieces the generator-pipe authoring path is
built from. `GeneratorKrea2Pipe` is the concrete core
Krea-2 generator pipe, re-exported so a plugin can subclass it
(as krea2-edit does) to reuse its config schema and
the shared `build_context`/seed-loop machinery instead of re-deriving the
family's own defaults.

See docs/plugin-api.md.
"""

from typing import Protocol, runtime_checkable

import numpy as np
import torch

from src.pipelines.pipes._shared.generation.generator_base import GeneratorContext
from src.pipelines.pipes._shared.generation.progress import ProgressEmitter, native_step_hooks
from src.pipelines.pipes.generator.krea2.main import GeneratorKrea2Pipe
from src.platform.runtime.native.engine import Conditioning


@runtime_checkable
class NativeGeneratorHandle(Protocol):
    """The narrow, stable slice of `NativeGenerator` a generator pipe's
    `generate_one` calls directly: encode/sample/decode plus the
    latent-shape helper, nothing else. A real `NativeGenerator` instance
    satisfies this structurally - no wrapping, no runtime overhead."""

    def encode_image(
        self, image: "np.ndarray | torch.Tensor", *, vram_free_gb: "float | None" = None
    ) -> torch.Tensor: ...

    def latent_shape_for(self, width: int, height: int, batch: int = 1) -> tuple: ...

    def sample(
        self,
        conditioning: "Conditioning | dict | tuple",
        latents_shape: tuple,
        steps: int,
        seed: int,
        cfg_scale: float,
        sampler: str = "euler",
        hooks=(),
        is_cancelled=None,
        denoise_strength: float = 1.0,
        noise: "torch.Tensor | None" = None,
        init_latent: "torch.Tensor | None" = None,
        guidance_options: "dict | None" = None,
        sampler_options: "dict | None" = None,
        step_cache_options: "dict | None" = None,
        warm_start: bool = False,
        schedule_settings: "dict | None" = None,
        spectral_progressive: "dict | None" = None,
    ) -> torch.Tensor: ...

    def decode(self, latents: torch.Tensor, *, vram_free_gb: "float | None" = None) -> np.ndarray: ...


__all__ = [
    "Conditioning",
    "GeneratorContext",
    "GeneratorKrea2Pipe",
    "NativeGeneratorHandle",
    "ProgressEmitter",
    "native_step_hooks",
]
