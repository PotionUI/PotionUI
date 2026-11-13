# Derived from: comfy_extras/nodes_minimax_h3.py (ComfyUI, GPL-3.0) — the MiniMax-H3-Image reference sizing (`_size_short_edge`, `_resize_to`) and the default canvas of MiniMaxH3ImageConditioning.
from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

CANVAS_MULTIPLE = 32
REFERENCE_SHORT_EDGE = 1024
REFERENCE_VAE_MAX_SHORT_EDGE = 2048
DEFAULT_SIZE = (2048, 2048)
MAX_REFERENCES = 16
AUTO_RESOLUTION = "auto"


def short_edge_size(width: int, height: int, low: int, high: int) -> tuple[int, int]:
    short = min(width, height)
    scale = min(max(short, low), high) / short
    return tuple(max(CANVAS_MULTIPLE, round(side * scale / CANVAS_MULTIPLE) * CANVAS_MULTIPLE) for side in (width, height))


def vae_reference_size(width: int, height: int) -> tuple[int, int]:
    return short_edge_size(width, height, REFERENCE_SHORT_EDGE, REFERENCE_VAE_MAX_SHORT_EDGE)


def vision_reference_size(width: int, height: int) -> tuple[int, int]:
    return short_edge_size(width, height, REFERENCE_SHORT_EDGE, REFERENCE_SHORT_EDGE)


def image_size(image: Any) -> tuple[int, int]:
    if isinstance(image, Image.Image):
        return image.size
    height, width = np.asarray(image).shape[:2]
    return int(width), int(height)


def as_rgb(image: Any) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    array = np.asarray(image)
    if array.dtype != np.uint8:
        array = (np.clip(array.astype(np.float32), 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return Image.fromarray(array[..., :3])


def resize_to(image: Any, size: tuple[int, int]) -> Image.Image:
    rgb = as_rgb(image)
    if rgb.size == size:
        return rgb
    return rgb.resize(size, Image.LANCZOS)


def resolve_canvas(resolution: str, references: list[Any]) -> tuple[int, int]:
    if str(resolution).strip().lower() != AUTO_RESOLUTION:
        width, height = (int(part) for part in str(resolution).lower().split("x"))
        return width, height
    if not references:
        return DEFAULT_SIZE
    return vae_reference_size(*as_rgb(references[0]).size)
