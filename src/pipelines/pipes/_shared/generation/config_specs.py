from __future__ import annotations

from typing import List

from src.pipelines.contracts import PipeConfigSpec

DEFAULT_PREVIEW_DESCRIPTION = "Emit live latent previews to the workbench during sampling"
DEFAULT_PREVIEW_DECODER_DESCRIPTION = "Tiny preview decoder weights (vae_approx); blank uses the latent colour projection"
DEFAULT_NAG_SCALE_DESCRIPTION = (
    "Normalized Attention Guidance scale (1.0 = off). Injects the negative "
    "prompt into cross-attention so it's enforced even at cfg=1.0 (single-forward speed) — set "
    "cfg to 1.0 and nag_scale to ~1.1-1.5 for the speed win; NAG stacks with true CFG if both are on."
)


def device_config_spec() -> PipeConfigSpec:
    return PipeConfigSpec("device", str, "cuda", "Compute device", required=False, choices=["cuda", "cpu"])


def dtype_config_spec() -> PipeConfigSpec:
    return PipeConfigSpec("dtype", str, "bfloat16", "Compute dtype", required=False,
                          choices=["bfloat16", "float16", "float32"])


def vram_limit_config_spec() -> PipeConfigSpec:
    return PipeConfigSpec("vram_limit_gb", float, None, "VRAM budget hint (backend-injected)", required=False)


def seed_config_spec(description: str = "Random seed") -> PipeConfigSpec:
    return PipeConfigSpec("seed", int, -1, description, required=False, min_value=-1)


def preview_config_spec(description: str = DEFAULT_PREVIEW_DESCRIPTION) -> PipeConfigSpec:
    return PipeConfigSpec("preview", bool, True, description, required=False)


def preview_decoder_config_spec(description: str = DEFAULT_PREVIEW_DECODER_DESCRIPTION) -> PipeConfigSpec:
    return PipeConfigSpec("preview_decoder", str, "", description, required=False)


def loras_config_spec(description: str) -> PipeConfigSpec:
    return PipeConfigSpec("loras", list, [], description, required=False)


def nag_config_specs(scale_description: str = DEFAULT_NAG_SCALE_DESCRIPTION) -> List[PipeConfigSpec]:
    return [
        PipeConfigSpec("nag_scale", float, 1.0, scale_description, required=False,
                       min_value=1.0, max_value=20.0),
        PipeConfigSpec("nag_tau", float, 3.5, "NAG norm-clamp threshold (paper default 3.5)", required=False,
                       min_value=0.1, max_value=20.0),
        PipeConfigSpec("nag_alpha", float, 0.5, "NAG blend-back-toward-positive weight (paper default 0.5)",
                       required=False, min_value=0.0, max_value=1.0),
    ]
