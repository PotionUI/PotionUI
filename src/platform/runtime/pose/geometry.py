from __future__ import annotations

import math
from typing import Tuple

import numpy as np

FIT_MODES = ("contain", "cover")


def snap(value: float, multiple: int, floor: bool = False) -> int:
    multiple = max(1, int(multiple))
    units = math.floor(value / multiple) if floor else round(value / multiple)
    return max(multiple, int(units) * multiple)


def resolve_output_size(
    source_width: int,
    source_height: int,
    width: int = 0,
    height: int = 0,
    max_pixels: int = 0,
    multiple: int = 32,
) -> Tuple[int, int]:
    if width > 0 and height > 0:
        return snap(width, multiple), snap(height, multiple)
    if width > 0:
        return snap(width, multiple), snap(width * source_height / source_width, multiple)
    if height > 0:
        return snap(height * source_width / source_height, multiple), snap(height, multiple)
    w, h = float(source_width), float(source_height)
    if max_pixels > 0 and w * h > max_pixels:
        shrink = math.sqrt(max_pixels / (w * h))
        base_w, base_h = snap(w * shrink, multiple, floor=True), snap(h * shrink, multiple, floor=True)
        aspect = math.log(w / h)
        candidates = [
            (cw, ch)
            for cw in (base_w, base_w + multiple)
            for ch in (base_h, base_h + multiple)
            if cw * ch <= max_pixels
        ]
        if candidates:
            return min(candidates, key=lambda size: abs(math.log(size[0] / size[1]) - aspect))
        out_w, out_h = base_w, base_h
        while out_w * out_h > max_pixels and (out_w > multiple or out_h > multiple):
            if out_w >= out_h and out_w > multiple:
                out_w -= multiple
            else:
                out_h -= multiple
        return out_w, out_h
    return snap(w, multiple), snap(h, multiple)


def fit_transform(
    source_width: int, source_height: int, width: int, height: int, mode: str = "contain",
) -> Tuple[float, float, float]:
    if mode not in FIT_MODES:
        raise ValueError(f"fit must be one of {FIT_MODES}, got {mode!r}")
    ratios = (width / source_width, height / source_height)
    scale = min(ratios) if mode == "contain" else max(ratios)
    return scale, (width - source_width * scale) / 2.0, (height - source_height * scale) / 2.0


def map_points(poses: np.ndarray, scale_x: float, scale_y: float, offset_x: float, offset_y: float) -> np.ndarray:
    out = poses.copy()
    out[..., 0] = poses[..., 0] * scale_x + offset_x
    out[..., 1] = poses[..., 1] * scale_y + offset_y
    return out
