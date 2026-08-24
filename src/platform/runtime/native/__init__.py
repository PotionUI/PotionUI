"""PotionUI native engine v2 — offline single-file model inference.

This package is self-contained: it loads diffusion models, text encoders and
VAEs from single ``.safetensors`` files by structural state-dict detection, with
no config.json and no network access.

Foundation surface: loading, detection, the ModelSpec registry, the ops
namespaces, and the arch base contract. Arch modules, text encoders, VAE and
sampling are built on top of these interfaces.
"""

from __future__ import annotations

from .base import NativeArchModule, load_into_module
from .detect.registry import ArchRegistry, ModelSpec, arch_registry, match_model_spec
from .engine import (
    Conditioning,
    NativeEngineLoader,
    NativeGenerator,
    NativeModel,
)
from .memory.device_plan import DevicePlan, make_device_plan
from .memory.tiering import ComponentPlacement, PlacementPlan, plan_placement
from .detect.te_detect import detect_te_config
from .detect.unet_detect import detect_unet_config
from .detect.vae_detect import detect_vae_config
from .errors import (
    NativeEngineError,
    NativeEngineLoadIntegrityError,
    NativeEngineUnsupportedError,
)
from .io.safetensors_loader import load_torch_file
from .ops.dtype import pick_dtypes
from vendor.gpl.comfyui.ops import detect_quant_format, pick_operations

__all__ = [
    "ArchRegistry",
    "ComponentPlacement",
    "Conditioning",
    "DevicePlan",
    "ModelSpec",
    "NativeArchModule",
    "NativeEngineError",
    "NativeEngineLoadIntegrityError",
    "NativeEngineLoader",
    "NativeEngineUnsupportedError",
    "NativeGenerator",
    "NativeModel",
    "PlacementPlan",
    "arch_registry",
    "detect_quant_format",
    "detect_te_config",
    "detect_unet_config",
    "detect_vae_config",
    "load_into_module",
    "load_torch_file",
    "make_device_plan",
    "match_model_spec",
    "pick_dtypes",
    "pick_operations",
    "plan_placement",
]
