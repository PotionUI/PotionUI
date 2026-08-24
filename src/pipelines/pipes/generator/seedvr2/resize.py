"""Input geometry for SeedVR2: area-resize toward a target area, then /16 crop.

Faithful to ByteDance's SeedVR preprocessing (``data/image/transforms/
area_resize.py`` + ``divisible_crop.py``, Apache-2.0):

  * **AreaResize** — a UNIFORM scale ``sqrt(target_area / (h*w))`` applied to both
    axes (bicubic), preserving aspect ratio. SeedVR2 does NOT change spatial
    resolution inside the model: the "upscale" IS this bicubic resize toward the
    target area, and the DiT then restores detail at that resolution. We keep the
    reference default (``downsample_only=False``) so the input is always scaled
    toward the requested area.
  * **DivisibleCrop** — center-crop the resized image to a multiple of 16 on each
    axis (the DiT patchify granularity: VAE 8x downsample x patch 2).

The target area is expressed two ways for an upscaler UI (see
:func:`target_area`): a ``scale`` factor (output area = ``scale^2`` x input area)
or an explicit ``target_short_side`` in pixels (``0`` -> use ``scale``).
"""

from __future__ import annotations

import math

from PIL import Image

CROP_MULTIPLE = 16


def target_area(width: int, height: int, scale: float, target_short_side: int) -> float:
    """Target pixel area for AreaResize.

    ``target_short_side > 0`` wins: the short axis becomes ``target_short_side``
    (uniform scale ``target_short_side / min(h, w)``), so the area is that scale
    squared times the input area. Otherwise the ``scale`` factor is used directly
    (output area = ``scale^2`` x input area).
    """
    if target_short_side and target_short_side > 0:
        s = target_short_side / max(1, min(width, height))
    else:
        s = max(1e-6, float(scale))
    return (width * s) * (height * s)


def area_resize(image: Image.Image, max_area: float) -> Image.Image:
    """Uniform bicubic resize so the output area approaches ``max_area``."""
    width, height = image.size
    s = math.sqrt(max_area / max(1, height * width))
    new_w, new_h = round(width * s), round(height * s)
    if (new_w, new_h) == (width, height):
        return image
    return image.resize((new_w, new_h), Image.BICUBIC)


def divisible_crop(image: Image.Image, factor: int = CROP_MULTIPLE) -> Image.Image:
    """Center-crop to a multiple of ``factor`` on each axis."""
    width, height = image.size
    crop_w = width - (width % factor)
    crop_h = height - (height % factor)
    if (crop_w, crop_h) == (width, height):
        return image
    left = (width - crop_w) // 2
    top = (height - crop_h) // 2
    return image.crop((left, top, left + crop_w, top + crop_h))


def prepare_input(
    image: Image.Image, scale: float, target_short_side: int, factor: int = CROP_MULTIPLE
) -> Image.Image:
    """Area-resize toward the target area, then center-crop to ``/factor``.

    Returns the RGB image the VAE encodes and the color-correction source.
    """
    rgb = image.convert("RGB") if image.mode != "RGB" else image
    area = target_area(rgb.width, rgb.height, scale, target_short_side)
    resized = area_resize(rgb, area)
    return divisible_crop(resized, factor)
