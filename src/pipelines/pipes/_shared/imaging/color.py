"""Colour helpers shared by the image-utility pipes: hex parsing, the
border-ring auto key-colour pick, RGB -> chrominance conversion, and
green/blue-screen despill. Pure numpy/PIL - no torch, no GPU.
"""

from typing import Tuple

import numpy as np
from PIL import ImageColor

#: Ring thickness (px) sampled from each edge to auto-pick a key colour. Small
#: enough to stay outside most subjects' silhouettes on a centered
#: composition, thick enough to outvote a few stray anti-aliased border
#: pixels.
BORDER_RING_PX = 6


def parse_hex_color(color: str) -> Tuple[int, int, int]:
    try:
        r, g, b = ImageColor.getcolor(color, "RGB")
    except ValueError as exc:
        raise ValueError(f"Invalid colour: {color!r}") from exc
    return (r, g, b)


def border_ring_color(rgb: np.ndarray) -> Tuple[int, int, int]:
    """Modal RGB across a thin ring at the image border (``key_mode: "auto"``
    colour pick)."""
    height, width = rgb.shape[0], rgb.shape[1]
    ring = min(BORDER_RING_PX, height // 2, width // 2)
    ring = max(ring, 1)

    pixels = np.concatenate([
        rgb[:ring, :, :].reshape(-1, 3),
        rgb[-ring:, :, :].reshape(-1, 3),
        rgb[:, :ring, :].reshape(-1, 3),
        rgb[:, -ring:, :].reshape(-1, 3),
    ])

    values, counts = np.unique(pixels, axis=0, return_counts=True)
    mode = values[np.argmax(counts)]
    return (int(mode[0]), int(mode[1]), int(mode[2]))


def rgb_to_cbcr(rgb: np.ndarray) -> np.ndarray:
    """RGB ``(..., 3)`` -> stacked ``(Cb, Cr)`` chrominance planes (ITU-R
    BT.601), luma (Y) discarded on purpose.

    A neutral gray ``(v, v, v)`` maps to exactly ``(128, 128)`` regardless of
    ``v`` - the Cb/Cr linear coefficients sum to zero - so brightness never
    moves a pixel's position in this plane on its own; only a change in hue
    or saturation does. That is what makes distance in this plane
    brightness-invariant where Euclidean RGB distance is not.
    """
    r = rgb[..., 0].astype(np.float64)
    g = rgb[..., 1].astype(np.float64)
    b = rgb[..., 2].astype(np.float64)
    cb = -0.168736 * r - 0.331264 * g + 0.5 * b + 128.0
    cr = 0.5 * r - 0.418688 * g - 0.081312 * b + 128.0
    return np.stack([cb, cr], axis=-1)


def despill(rgb: np.ndarray, key_rgb: Tuple[int, int, int]) -> np.ndarray:
    """Suppress the key colour's dominant channel toward the average of the
    other two, on every pixel - the classic green/blue-screen spill fix,
    generalized to whichever channel the key colour peaks on."""
    dominant = int(np.argmax(key_rgb))
    others = [c for c in (0, 1, 2) if c != dominant]

    out = rgb.astype(np.float64).copy()
    avg_others = (out[..., others[0]] + out[..., others[1]]) / 2.0
    out[..., dominant] = np.minimum(out[..., dominant], avg_others)
    return np.clip(out, 0, 255).astype(np.uint8)
