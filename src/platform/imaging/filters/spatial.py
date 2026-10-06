from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from src.platform.imaging.filters.colour import LUMA, smoothstep
from src.platform.imaging.filters.lut import FilterOpUnavailable, round_half_up
from src.platform.imaging.filters.ops import CORE_OPS, OpSpec, resolve_params, split_steps

_U32 = np.uint32


def lowbias32(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=_U32)
    with np.errstate(over="ignore"):
        x = x ^ (x >> _U32(16))
        x = x * _U32(0x7FEB352D)
        x = x ^ (x >> _U32(15))
        x = x * _U32(0x846CA68B)
        x = x ^ (x >> _U32(16))
    return x


def grain_cell(size: float, width: int, height: int) -> int:
    return max(1, int(math.floor(size * min(width, height) / 1000 + 0.5)))


def grain_noise(cx: np.ndarray, cy: np.ndarray, seed: int) -> np.ndarray:
    with np.errstate(over="ignore"):
        mixed = (
            (np.asarray(cx, dtype=np.int64).astype(_U32) * _U32(73856093))
            ^ (np.asarray(cy, dtype=np.int64).astype(_U32) * _U32(19349663))
            ^ _U32(((seed + 1) * 83492791) & 0xFFFFFFFF)
        )
    h = lowbias32(mixed)
    second = lowbias32(h ^ _U32(0x9E3779B9))
    return (h.astype(np.float64) / 4294967296 + second.astype(np.float64) / 4294967296) - 1


def vignette(rgb: np.ndarray, params: Mapping[str, Any], k: float) -> np.ndarray:
    height, width = rgb.shape[:2]
    diag = math.hypot(width, height)
    a = (params["amount"] / 100) * k
    e0 = params["midpoint"] / 100
    e1 = e0 + max(0.05, params["feather"] / 100)
    dx = (np.arange(width, dtype=np.float64) + 0.5) - width / 2
    dy = (np.arange(height, dtype=np.float64) + 0.5) - height / 2
    r = (np.hypot(dx[None, :], dy[:, None]) / diag) * 2
    w = smoothstep(e0, e1, r)[:, :, None]
    v = rgb.astype(np.float64) / 255
    if a >= 0:
        out = v * (1 - a * w)
    else:
        out = v + (1 - v) * -a * w
    return round_half_up(np.clip(out, 0.0, 1.0) * 255).astype(np.uint8)


def grain(rgb: np.ndarray, params: Mapping[str, Any], k: float) -> np.ndarray:
    height, width = rgb.shape[:2]
    amount = (params["amount"] / 100) * k
    cell = grain_cell(params["size"], width, height)
    cells_x = -(-width // cell)
    cells_y = -(-height // cell)
    gx, gy = np.meshgrid(np.arange(cells_x), np.arange(cells_y))
    noise = grain_noise(gx, gy, int(params["seed"]))
    noise = np.repeat(np.repeat(noise, cell, axis=0), cell, axis=1)[:height, :width]
    px = rgb.astype(np.float64)
    luma = (LUMA[0] * px[:, :, 0] + LUMA[1] * px[:, :, 1] + LUMA[2] * px[:, :, 2]) / 255
    t = 2 * luma - 1
    mid = 1 - 0.7 * (t * t)
    delta = (noise * amount * 0.14 * mid * 255)[:, :, None]
    return np.clip(np.floor(px + delta + 0.5), 0, 255).astype(np.uint8)


CORE_SPATIAL = {"vignette": vignette, "grain": grain}


def apply_spatial(
    rgb: np.ndarray,
    steps: Sequence[Mapping[str, Any]],
    intensity: float = 100,
    ops: Optional[Mapping[str, OpSpec]] = None,
    impls: Optional[Mapping[str, Any]] = None,
) -> np.ndarray:
    known = ops if ops is not None else CORE_OPS
    _, spatial_steps = split_steps(steps, known)
    k = intensity / 100
    out = rgb
    for step in spatial_steps:
        op_id = step["op"]
        params = resolve_params(known[op_id], step)
        if op_id in CORE_OPS:
            out = CORE_SPATIAL[op_id](out, params, k)
            continue
        impl = (impls or {}).get(op_id)
        if impl is None or not hasattr(impl, "apply"):
            raise FilterOpUnavailable(op_id)
        image = out.astype(np.float32) / 255
        result = np.asarray(impl.apply(image, params, k), dtype=np.float32)
        out = round_half_up(np.clip(result, 0.0, 1.0) * 255).astype(np.uint8)
    return out
