"""Architecture detection: DiT / text-encoder / VAE + the arch registry."""

from __future__ import annotations

from .registry import ArchRegistry, ModelSpec, arch_registry, match_model_spec
from .te_detect import detect_te_config
from .unet_detect import detect_unet_config
from .vae_detect import detect_vae_config

__all__ = [
    "ArchRegistry",
    "ModelSpec",
    "arch_registry",
    "detect_te_config",
    "detect_unet_config",
    "detect_vae_config",
    "match_model_spec",
]
