from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageFilter


def create_grain_layer(size: tuple, intensity: float, grain_size: float, rng: Any = np.random) -> np.ndarray:
    scale = 1 / grain_size
    scaled_size = (max(1, int(size[1] * scale)), max(1, int(size[0] * scale)))

    noise = rng.normal(0, 1, scaled_size)

    if scale != 1:
        noise = Image.fromarray((noise * 255).astype(np.uint8))
        noise = noise.resize((size[0], size[1]), Image.BILINEAR)
        noise = np.array(noise).astype(float) / 255

    noise = (noise - noise.mean()) / noise.std()
    noise = noise * intensity

    return noise


def apply_film_grain(
    image: Image.Image,
    *,
    intensity: float,
    grain_size: float,
    shadows_bias: float,
    highlights_protect: float,
    monochromatic: bool,
    blur_amount: float,
    rng: Any = np.random,
) -> Image.Image:
    img_array = np.array(image).astype(float) / 255
    height, width = img_array.shape[:2]

    grain = create_grain_layer((width, height), intensity, grain_size, rng)

    if monochromatic:
        grain = np.expand_dims(grain, axis=-1)
        grain = np.repeat(grain, 3, axis=-1)
    else:
        grain = np.stack([
            create_grain_layer((width, height), intensity, grain_size, rng)
            for _ in range(3)
        ], axis=-1)

    luminance = np.mean(img_array, axis=2)

    shadows_mask = 1 - (luminance ** (1 - shadows_bias))
    highlights_mask = 1 - (1 - luminance) ** (1 - highlights_protect)

    final_mask = shadows_mask * highlights_mask
    final_mask = np.stack([final_mask] * 3, axis=-1)
    grain = grain * final_mask

    result = img_array + grain
    result = np.clip(result, 0, 1)

    result_image = Image.fromarray((result * 255).astype(np.uint8))

    if blur_amount > 0:
        result_image = result_image.filter(ImageFilter.GaussianBlur(radius=blur_amount))

    return result_image
