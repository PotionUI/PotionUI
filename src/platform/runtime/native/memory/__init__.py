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
from .tiering import ComponentPlacement, PlacementPlan, plan_placement

__all__ = [
    "ComponentPlacement",
    "DevicePlan",
    "GpuResidencyRegistry",
    "PlacementPlan",
    "effective_free_vram_gb",
    "free_vram_gb",
    "total_vram_gb",
    "get_residency_registry",
    "make_device_plan",
    "minimum_inference_memory_gb",
    "module_size_gb",
    "plan_placement",
    "run_text_encode",
    "run_text_encode_batch",
]
