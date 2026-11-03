from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable, Literal

import torch
import torch.nn as nn

TextEncoderPlacementMode = Literal["auto", "gpu", "stream"]
TEXT_ENCODER_PLACEMENT_MODES: tuple[str, ...] = ("auto", "gpu", "stream")
DEFAULT_TEXT_ENCODER_PLACEMENT: TextEncoderPlacementMode = "auto"

_BYTES_PER_GB = 1024 ** 3
_FP8_DTYPES = (torch.float8_e4m3fn, torch.float8_e5m2)
DEQUANT_BYTES_PER_ELEMENT: dict[str, float] = {"nvfp4": 10.0, "int8": 8.0, "fp8": 8.0}


def normalize_text_encoder_placement(value: Any) -> TextEncoderPlacementMode:
    if value is None:
        return DEFAULT_TEXT_ENCODER_PLACEMENT
    word = str(value).strip().lower()
    if not word:
        return DEFAULT_TEXT_ENCODER_PLACEMENT
    if word not in TEXT_ENCODER_PLACEMENT_MODES:
        raise ValueError(
            f"text_encoder_placement must be one of {', '.join(TEXT_ENCODER_PLACEMENT_MODES)}, got {value!r}"
        )
    return word


@dataclass(frozen=True)
class TextEncoderPlacementSettings:
    mode: TextEncoderPlacementMode = DEFAULT_TEXT_ENCODER_PLACEMENT
    vram_budget_gb: float | None = None


_settings_lock = threading.Lock()
_settings = TextEncoderPlacementSettings()


def configure_text_encoder_placement(
    mode: Any = None, *, vram_budget_gb: float | None = None,
) -> TextEncoderPlacementSettings:
    global _settings
    budget = float(vram_budget_gb) if vram_budget_gb is not None and float(vram_budget_gb) > 0 else None
    new = TextEncoderPlacementSettings(mode=normalize_text_encoder_placement(mode), vram_budget_gb=budget)
    with _settings_lock:
        _settings = new
    return new


def text_encoder_placement_settings() -> TextEncoderPlacementSettings:
    return _settings


def text_encoder_placement_mode() -> TextEncoderPlacementMode:
    return _settings.mode


def reset_text_encoder_placement() -> None:
    global _settings
    with _settings_lock:
        _settings = TextEncoderPlacementSettings()


def text_encoder_modules(encoder: Any) -> list[nn.Module]:
    seen: set[int] = set()
    found: list[nn.Module] = []

    def add(candidate: Any) -> None:
        inner = getattr(candidate, "module", candidate)
        if isinstance(inner, nn.Module) and id(inner) not in seen:
            seen.add(id(inner))
            found.append(inner)

    for attr in ("module", "t5", "clip_l"):
        sub = getattr(encoder, attr, None)
        if sub is not None:
            add(sub)
    if not found:
        add(encoder)
    return found


def leaf_quant_format(module: nn.Module) -> str | None:
    if getattr(module, "_is_nvfp4", False) and getattr(module, "nvfp4_packed", None) is not None:
        return "nvfp4"
    weight = getattr(module, "weight", None)
    if not isinstance(weight, torch.Tensor) or getattr(module, "weight_scale", None) is None:
        return None
    if weight.dtype == torch.int8:
        return "int8"
    if weight.dtype in _FP8_DTYPES:
        return "fp8"
    return None


def _dequant_elements(module: nn.Module, fmt: str) -> int:
    if fmt == "nvfp4":
        out_features = getattr(module, "out_features", None)
        in_features = getattr(module, "in_features", None)
        if isinstance(out_features, int) and isinstance(in_features, int) and out_features > 0 and in_features > 0:
            return out_features * in_features
        return int(module.nvfp4_packed.numel()) * 2
    return int(module.weight.numel())


def tensors_bytes(tensors: Iterable[torch.Tensor | None], seen: set[int] | None = None) -> int:
    seen = set() if seen is None else seen
    total = 0
    for t in tensors:
        if t is None or id(t) in seen:
            continue
        seen.add(id(t))
        total += t.numel() * t.element_size()
    return total


def module_weights_bytes(module: nn.Module, seen: set[int] | None = None) -> int:
    return tensors_bytes(list(module.parameters()) + list(module.buffers()), seen)


@dataclass(frozen=True)
class TextEncoderFootprint:
    weights_gb: float
    dequant_peak_gb: float
    quant_formats: tuple[str, ...]

    @property
    def encode_gb(self) -> float:
        return self.weights_gb + self.dequant_peak_gb


def text_encoder_footprint(encoder: Any) -> TextEncoderFootprint | None:
    modules = text_encoder_modules(encoder)
    if not modules:
        return None
    seen: set[int] = set()
    total = 0
    peak = 0.0
    formats: set[str] = set()
    for module in modules:
        try:
            total += module_weights_bytes(module, seen)
        except (TypeError, ValueError, RuntimeError):
            return None
        for leaf in module.modules():
            fmt = leaf_quant_format(leaf)
            if fmt is None:
                continue
            formats.add(fmt)
            peak = max(peak, _dequant_elements(leaf, fmt) * DEQUANT_BYTES_PER_ELEMENT[fmt])
    if total <= 0:
        return None
    return TextEncoderFootprint(
        weights_gb=total / _BYTES_PER_GB,
        dequant_peak_gb=peak / _BYTES_PER_GB,
        quant_formats=tuple(sorted(formats)),
    )


def encoder_gb_on_device(encoder: Any, device: str | torch.device) -> float:
    target = torch.device(device)
    index = target.index if target.index is not None else 0
    seen: set[int] = set()
    total = 0
    for module in text_encoder_modules(encoder):
        for t in list(module.parameters()) + list(module.buffers()):
            if t is None or id(t) in seen:
                continue
            seen.add(id(t))
            dev = t.device
            if dev.type == target.type and (dev.type != "cuda" or (dev.index or 0) == index):
                total += t.numel() * t.element_size()
    return total / _BYTES_PER_GB
