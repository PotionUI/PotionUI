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


LUMINANCE_LEVELS = 3 * 255 + 1
UNIFORM_BYTE_MEAN = 127.5
SPREAD_SAMPLES = 16384


def grain_mask(luminance: np.ndarray, shadows_bias: float, highlights_protect: float) -> np.ndarray:
    shadows_mask = 1 - (luminance ** (1 - shadows_bias))
    highlights_mask = 1 - (1 - luminance) ** (1 - highlights_protect)
    return shadows_mask * highlights_mask


def _grain_fast(
    frame: np.ndarray,
    intensity: float,
    grain_size: float,
    shadows_bias: float,
    highlights_protect: float,
    monochromatic: bool,
    rng: Any,
) -> np.ndarray:
    import cv2

    if not isinstance(rng, np.random.Generator):
        rng = np.random.default_rng()
    height, width = frame.shape[:2]
    channels = 1 if monochromatic else 3
    scale = min(1.0, 1 / grain_size)
    low = (max(1, int(height * scale)), max(1, int(width * scale)), channels)

    noise = rng.integers(0, 256, low, dtype=np.uint8)
    if low[:2] != (height, width):
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)
    noise = noise.astype(np.float32).reshape(height, width, channels)
    noise -= UNIFORM_BYTE_MEAN

    stride = max(1, int((height * width / SPREAD_SAMPLES) ** 0.5))
    spread = float(noise[::stride, ::stride].std())
    gain = intensity * 255 / spread if spread > 0 else 0.0

    levels = np.arange(LUMINANCE_LEVELS, dtype=np.float32) / (LUMINANCE_LEVELS - 1)
    lut = (grain_mask(levels, shadows_bias, highlights_protect) * gain).astype(np.float32)
    total = frame[..., 0].astype(np.uint16)
    total += frame[..., 1]
    total += frame[..., 2]
    noise *= np.take(lut, total)[..., None]

    if channels == 1:
        noise = cv2.cvtColor(noise.reshape(height, width), cv2.COLOR_GRAY2RGB)
    return cv2.add(frame, noise, dtype=cv2.CV_8U)


def apply_film_grain(
    image: Image.Image | np.ndarray,
    *,
    intensity: float,
    grain_size: float,
    shadows_bias: float,
    highlights_protect: float,
    monochromatic: bool,
    blur_amount: float,
    rng: Any = np.random,
    fast: bool = False,
) -> Image.Image | np.ndarray:
    if fast:
        is_image = isinstance(image, Image.Image)
        grained = _grain_fast(
            np.asarray(image), intensity, grain_size, shadows_bias, highlights_protect, monochromatic, rng,
        )
        if not is_image and blur_amount <= 0:
            return grained
        result_image = Image.fromarray(grained)
        if blur_amount > 0:
            result_image = result_image.filter(ImageFilter.GaussianBlur(radius=blur_amount))
        return result_image if is_image else np.asarray(result_image)

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

    final_mask = grain_mask(luminance, shadows_bias, highlights_protect)
    final_mask = np.stack([final_mask] * 3, axis=-1)
    grain = grain * final_mask

    result = img_array + grain
    result = np.clip(result, 0, 1)

    result_image = Image.fromarray((result * 255).astype(np.uint8))

    if blur_amount > 0:
        result_image = result_image.filter(ImageFilter.GaussianBlur(radius=blur_amount))

    return result_image
