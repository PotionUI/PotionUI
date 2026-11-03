"""Memory placement: VRAM tiering and per-component device assignment."""

from __future__ import annotations

from .device_plan import DevicePlan, make_device_plan
from .residency import (
    GpuResidencyRegistry,
    effective_free_vram_gb,
    free_vram_gb,
    total_vram_gb,
    get_residency_registry,
    minimum_inference_memory_gb,
    module_size_gb,
    run_text_encode,
    run_text_encode_batch,
)
from .text_encoder import (
    TEXT_ENCODER_PLACEMENT_MODES,
    TextEncoderFootprint,
    configure_text_encoder_placement,
    text_encoder_footprint,
    text_encoder_placement_mode,
)
from .tiering import ComponentPlacement, PlacementPlan, plan_placement, plan_text_encoder_placement

__all__ = [
    "ComponentPlacement",
    "DevicePlan",
    "GpuResidencyRegistry",
    "PlacementPlan",
    "TEXT_ENCODER_PLACEMENT_MODES",
    "TextEncoderFootprint",
    "configure_text_encoder_placement",
    "effective_free_vram_gb",
    "free_vram_gb",
    "total_vram_gb",
    "get_residency_registry",
    "make_device_plan",
    "minimum_inference_memory_gb",
    "module_size_gb",
    "plan_placement",
    "plan_text_encoder_placement",
    "run_text_encode",
    "run_text_encode_batch",
    "text_encoder_footprint",
    "text_encoder_placement_mode",
]
