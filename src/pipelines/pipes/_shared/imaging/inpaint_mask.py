from __future__ import annotations

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image


def mask_to_unit_array(mask: Image.Image, size: tuple[int, int]) -> np.ndarray:
    resized = mask.convert("L")
    if resized.size != tuple(size):
        resized = resized.resize(tuple(size), Image.BILINEAR)
    return np.asarray(resized, dtype=np.float32) / 255.0


def latent_mask_from_array(mask: np.ndarray, latent_shape: tuple[int, ...]) -> torch.Tensor:
    height, width = int(latent_shape[-2]), int(latent_shape[-1])
    pooled = F.adaptive_avg_pool2d(torch.from_numpy(np.ascontiguousarray(mask))[None, None], (height, width))
    return pooled.reshape(*([1] * (len(latent_shape) - 2)), height, width)


def soft_mask(mask: np.ndarray, feather_fraction: float) -> np.ndarray:
    region = mask >= 0.5
    if feather_fraction <= 0 or not region.any() or region.all():
        return mask
    ys, xs = np.nonzero(region)
    size = max(int(ys.max() - ys.min()) + 1, int(xs.max() - xs.min()) + 1)
    feather_px = max(4.0, float(feather_fraction) * size)
    outside = cv2.distanceTransform((~region).astype(np.uint8), cv2.DIST_L2, 3)
    t = np.clip(outside / feather_px, 0.0, 1.0)
    ramp = 1.0 - t * t * (3.0 - 2.0 * t)
    return np.where(region, 1.0, ramp).astype(np.float32)


def composite_pixels(generated: np.ndarray, source: np.ndarray, mask: np.ndarray) -> np.ndarray:
    generated = generated[..., :3]
    weight = mask[..., None]
    blended = generated.astype(np.float32) * weight + source.astype(np.float32) * (1.0 - weight)
    out = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    return np.where(weight > 0, out, source)
