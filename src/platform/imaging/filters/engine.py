from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Union

import numpy as np
from PIL import Image

from src.platform.imaging.filters.cube import Cube
from src.platform.imaging.filters.lut import Lut, apply_lut, compile_lut
from src.platform.imaging.filters.ops import OpSpec
from src.platform.imaging.filters.spatial import apply_spatial

ImageLike = Union[np.ndarray, Image.Image]


def _apply_array(
    pixels: np.ndarray,
    steps: Sequence[Mapping[str, Any]],
    intensity: float,
    lut: Lut,
    ops: Optional[Mapping[str, OpSpec]],
    impls: Optional[Mapping[str, Any]],
) -> np.ndarray:
    out = pixels.copy()
    if intensity <= 0:
        return out
    rgb = apply_lut(np.ascontiguousarray(pixels[..., :3]), lut)
    rgb = apply_spatial(rgb, steps, intensity, ops, impls)
    out[..., :3] = rgb
    return out


def apply_filter(
    image: ImageLike,
    steps: Sequence[Mapping[str, Any]],
    intensity: float = 100,
    cube: Optional[Cube] = None,
    lut: Optional[Lut] = None,
    ops: Optional[Mapping[str, OpSpec]] = None,
    impls: Optional[Mapping[str, Any]] = None,
) -> ImageLike:
    compiled = lut if lut is not None else compile_lut(steps, cube, intensity, ops, impls)
    if isinstance(image, Image.Image):
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        pixels = np.asarray(image.convert(mode), dtype=np.uint8)
        return Image.fromarray(_apply_array(pixels, steps, intensity, compiled, ops, impls), mode)
    return _apply_array(image, steps, intensity, compiled, ops, impls)
