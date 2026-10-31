from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from src.platform.runtime.gpu_generation import GpuGeneration, generation_for

GENERATION_NONE = GpuGeneration.NONE.value
GENERATION_AMPERE = GpuGeneration.AMPERE.value
GENERATION_ADA = GpuGeneration.ADA.value
GENERATION_HOPPER = GpuGeneration.HOPPER.value
GENERATION_BLACKWELL = GpuGeneration.BLACKWELL.value
GENERATION_OTHER = "other"

GPU_GENERATIONS = (
    GENERATION_AMPERE,
    GENERATION_ADA,
    GENERATION_HOPPER,
    GENERATION_BLACKWELL,
    GENERATION_OTHER,
)

PRECISIONS = ("bf16", "fp16", "fp8", "int8", "nvfp4")

GENERATION_LABELS = {
    GENERATION_AMPERE: "RTX 30-series (Ampere)",
    GENERATION_ADA: "RTX 40-series (Ada)",
    GENERATION_HOPPER: "Hopper",
    GENERATION_BLACKWELL: "RTX 50-series (Blackwell)",
    GENERATION_OTHER: "this GPU",
    GENERATION_NONE: "no GPU",
}

_FAST_EVERYWHERE = ("bf16", "fp16")
_FAST_EXTRA = {
    GENERATION_ADA: ("fp8",),
    GENERATION_HOPPER: ("fp8",),
    GENERATION_BLACKWELL: ("fp8",),
}
_NVFP4_FAST_MIN_CAPABILITY = (12, 0)
_SUPPORTED_ONLY_ON = {"nvfp4": (GENERATION_BLACKWELL,)}


@dataclass(frozen=True)
class GpuProfile:
    generation: str = GENERATION_NONE
    name: Optional[str] = None
    vram_gb: float = 0.0
    compute_capability: Optional[Tuple[int, int]] = None
    fast_precisions: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_gpu(self) -> bool:
        return self.generation != GENERATION_NONE

    @property
    def vram_class_gb(self) -> int:
        return int(round(self.vram_gb))

    @property
    def generation_label(self) -> str:
        return GENERATION_LABELS.get(self.generation, self.generation)

    def is_fast(self, precision: str) -> bool:
        return precision in self.fast_precisions

    def supports(self, precision: str) -> bool:
        if not self.has_gpu:
            return False
        allowed = _SUPPORTED_ONLY_ON.get(precision)
        return allowed is None or self.generation in allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "generation_label": self.generation_label,
            "name": self.name,
            "vram_gb": self.vram_gb,
            "compute_capability": (
                f"{self.compute_capability[0]}.{self.compute_capability[1]}"
                if self.compute_capability
                else None
            ),
            "fast_precisions": list(self.fast_precisions),
        }


def generation_for_capability(capability: Optional[Tuple[int, int]]) -> str:
    if not capability:
        return GENERATION_NONE
    generation = generation_for(capability).value
    return generation if generation in GPU_GENERATIONS else GENERATION_OTHER


def generations_supporting(precision: str) -> Tuple[str, ...]:
    return _SUPPORTED_ONLY_ON.get(precision, ())


def fast_precisions_for(generation: str, capability: Optional[Tuple[int, int]]) -> Tuple[str, ...]:
    if generation == GENERATION_NONE:
        return ()
    fast = _FAST_EVERYWHERE + _FAST_EXTRA.get(generation, ())
    if generation == GENERATION_BLACKWELL and capability and tuple(capability) >= _NVFP4_FAST_MIN_CAPABILITY:
        fast += ("nvfp4",)
    return fast


def build_gpu_profile(
    capability: Optional[Tuple[int, int]],
    vram_gb: float,
    name: Optional[str] = None,
    vendor_is_nvidia: bool = True,
) -> GpuProfile:
    if capability is None and vram_gb <= 0:
        return GpuProfile()
    generation = generation_for_capability(capability) if vendor_is_nvidia else GENERATION_OTHER
    if generation == GENERATION_NONE:
        generation = GENERATION_OTHER
    return GpuProfile(
        generation=generation,
        name=name,
        vram_gb=round(float(vram_gb), 1),
        compute_capability=tuple(capability) if capability else None,
        fast_precisions=fast_precisions_for(generation, capability),
    )


def detect_gpu_profile(device_index: int = 0) -> GpuProfile:
    try:
        from src.platform.runtime.native.kernels import capabilities
        from src.platform.runtime.native.kernels.hardware import VENDOR_CUDA

        devices = capabilities().devices
        if not 0 <= device_index < len(devices):
            return GpuProfile()
        device = devices[device_index]
        if device.capability == (0, 0) or device.total_memory <= 0:
            return GpuProfile()
        return build_gpu_profile(
            device.capability,
            device.total_memory / (1024 ** 3),
            device.name,
            device.vendor == VENDOR_CUDA,
        )
    except Exception:
        return GpuProfile()
