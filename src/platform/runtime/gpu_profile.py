from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

GENERATION_NONE = "none"
GENERATION_AMPERE = "ampere"
GENERATION_ADA = "ada"
GENERATION_HOPPER = "hopper"
GENERATION_BLACKWELL = "blackwell"
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

_FAST_EVERYWHERE = ("bf16", "fp16", "int8")
_FAST_EXTRA = {
    GENERATION_ADA: ("fp8",),
    GENERATION_HOPPER: ("fp8",),
    GENERATION_BLACKWELL: ("fp8", "nvfp4"),
}
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
    major, minor = capability
    if major == 8 and minor == 9:
        return GENERATION_ADA
    if major == 8:
        return GENERATION_AMPERE
    if major == 9:
        return GENERATION_HOPPER
    if major in (10, 11, 12):
        return GENERATION_BLACKWELL
    return GENERATION_OTHER


def fast_precisions_for(generation: str) -> Tuple[str, ...]:
    if generation == GENERATION_NONE:
        return ()
    return _FAST_EVERYWHERE + _FAST_EXTRA.get(generation, ())


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
        fast_precisions=fast_precisions_for(generation),
    )


def detect_gpu_profile(device_index: int = 0) -> GpuProfile:
    try:
        import torch
    except Exception:
        return GpuProfile()
    try:
        if not torch.cuda.is_available():
            return GpuProfile()
        properties = torch.cuda.get_device_properties(device_index)
        capability = (int(properties.major), int(properties.minor))
        vram_gb = properties.total_memory / (1024 ** 3)
        vendor_is_nvidia = getattr(torch.version, "hip", None) is None
        return build_gpu_profile(capability, vram_gb, str(properties.name), vendor_is_nvidia)
    except Exception:
        return GpuProfile()
