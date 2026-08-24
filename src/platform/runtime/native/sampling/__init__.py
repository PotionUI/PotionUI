"""Unified flow-matching sampling core for the native engine.

One denoise loop covers every native target (Flux1/Flux2/Klein/Qwen-Image/
Wan/LTX): shift-scheduled sigmas per ModelSpec, guidance as strategy objects,
and StepHooks fired per step. See ``denoise_loop.denoise`` for the entry point.
"""

from .algorithms import (
    ANCESTRAL_NOISE_SEED_OFFSET,
    sample_dpmpp_2m,
    sample_euler,
    sample_euler_ancestral,
    sample_euler_restart,
    sample_euler_sde,
    sample_unipc,
)
from .cfg import EmbeddedGuidance, GuidanceStrategy, NoCFG, SkipLayerGuidance, TrueCFG
from .multimodal_guider import MultiModalGuidance, MultiModalGuiderParams, multimodal_combine
from .conditioned import conditioned_sigmas, denoise_prenoised
from .denoise_loop import SAMPLERS, STOCHASTIC_SAMPLERS, denoise, ensure_sampler_generator, make_guidance
from .flow_schedule import build_sigmas
from .hooks import BaseStepHook, PreviewHook, ProgressHook, StepHook, run_hooks
from .preview import (
    PREVIEW_EVERY_N,
    PREVIEW_MAX_SIZE,
    PreviewFactors,
    latent_to_preview_image,
    latent_to_rgb,
    make_preview_hook,
    resolve_preview_factors,
)

__all__ = [
    "build_sigmas",
    "denoise",
    "denoise_prenoised",
    "conditioned_sigmas",
    "make_guidance",
    "SAMPLERS",
    "STOCHASTIC_SAMPLERS",
    "ensure_sampler_generator",
    "sample_euler",
    "sample_euler_sde",
    "sample_euler_ancestral",
    "ANCESTRAL_NOISE_SEED_OFFSET",
    "sample_euler_restart",
    "sample_dpmpp_2m",
    "sample_unipc",
    "GuidanceStrategy",
    "EmbeddedGuidance",
    "TrueCFG",
    "NoCFG",
    "SkipLayerGuidance",
    "StepHook",
    "BaseStepHook",
    "ProgressHook",
    "PreviewHook",
    "run_hooks",
    "PREVIEW_EVERY_N",
    "PREVIEW_MAX_SIZE",
    "PreviewFactors",
    "latent_to_rgb",
    "latent_to_preview_image",
    "resolve_preview_factors",
    "make_preview_hook",
]
