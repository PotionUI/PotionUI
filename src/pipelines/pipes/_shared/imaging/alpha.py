"""Alpha-channel helpers shared by the image-utility pipes (``matting/birefnet``,
``crop_subject``, ``color_key``): bounding-box extraction from an alpha
channel, matte-strength smoothstep tightening, and Gaussian feathering. Pure
numpy/PIL - no torch, no GPU, deterministic.
"""

from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter


def alpha_bbox(image: Image.Image, threshold: int = 16) -> Optional[Tuple[int, int, int, int]]:
    """Bounding box ``(x, y, w, h)`` of pixels whose alpha exceeds ``threshold``.

    ``None`` when no pixel qualifies (a fully transparent, or fully
    below-threshold, image) - callers must not treat that as an error, it is
    the normal "nothing to crop to" case.
    """
    alpha = np.array(image.convert("RGBA"))[..., 3]
    mask = alpha > threshold

    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    if not rows.any() or not cols.any():
        return None

    y0, y1 = np.where(rows)[0][[0, -1]]
    x0, x1 = np.where(cols)[0][[0, -1]]
    return (int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1))


def apply_matte_strength(alpha: np.ndarray, strength: int) -> np.ndarray:
    """Levels/smoothstep tightening of a matting model's raw soft alpha.

    A matting model's raw output (e.g. BiRefNet's sigmoid probability, scaled
    to 0-255) carries no threshold of its own: a pixel the model is unsure
    about lands at a "wishy-washy" mid-gray value instead of committing to
    fully transparent or fully opaque. Left alone, that reads as flicker
    across a sequence where most frames matte cleanly but a few leave
    semi-transparent background residue.

    ``strength`` 0-100: 0 is the raw alpha unchanged (identity - every value
    maps to itself); 100 is a hard step at the midpoint (128): >=128 -> 255,
    <128 -> 0. Between the two, a smoothstep's transition band shrinks
    linearly from a half-width of 128 (no cut, strength 0) to 0 (hard
    threshold, strength 100) as strength rises.
    """
    strength = max(0, min(100, int(strength)))
    if strength <= 0:
        return alpha

    half_width = 128.0 * (1.0 - strength / 100.0)
    low = 128.0 - half_width
    high = 128.0 + half_width

    if high <= low:  # strength == 100: the smoothstep band has zero width.
        return np.where(alpha >= 128, 255, 0).astype(np.uint8)

    t = np.clip((alpha.astype(np.float64) - low) / (high - low), 0.0, 1.0)
    smoothed = t * t * (3.0 - 2.0 * t)
    return np.round(smoothed * 255.0).astype(np.uint8)


def feather_alpha(alpha: np.ndarray, feather_px: float) -> np.ndarray:
    """Gaussian-blur ``alpha`` (uint8, any shape) by ``feather_px``. ``<= 0``
    is the identity (a hard edge, unchanged)."""
    if feather_px <= 0:
        return alpha
    blurred = Image.fromarray(alpha, mode="L").filter(
        ImageFilter.GaussianBlur(radius=feather_px / 2.0)
    )
    return np.array(blurred)
