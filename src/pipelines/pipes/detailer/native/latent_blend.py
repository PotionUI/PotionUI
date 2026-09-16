from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import torch
from PIL import Image


def latent_mask_tensor(mask: Image.Image, latent_shape: Sequence[int]) -> torch.Tensor:
    height, width = int(latent_shape[-2]), int(latent_shape[-1])
    small = mask.convert("L").resize((width, height), Image.BOX)
    tensor = torch.from_numpy(np.asarray(small, dtype=np.float32) / 255.0)
    while tensor.ndim < len(latent_shape):
        tensor = tensor.unsqueeze(0)
    return tensor


class LatentInpaintFilter:
    priority = 200

    def __init__(self, original_latent: torch.Tensor, noise: torch.Tensor, mask: torch.Tensor):
        self._original = original_latent
        self._noise = noise
        self._mask = mask
        self._cached: Optional[tuple] = None

    def _tensors(self, x: torch.Tensor):
        key = (x.device, x.dtype)
        if self._cached is None or self._cached[0] != key:
            self._cached = (
                key,
                self._original.to(device=x.device, dtype=x.dtype),
                self._noise.to(device=x.device, dtype=x.dtype),
                self._mask.to(device=x.device, dtype=x.dtype),
            )
        return self._cached[1], self._cached[2], self._cached[3]

    def filter_latent(self, step_index: int, total_steps: int, x: torch.Tensor, sigma: float) -> torch.Tensor:
        original, noise, mask = self._tensors(x)
        original_at_sigma = (1.0 - sigma) * original + sigma * noise
        return mask * x + (1.0 - mask) * original_at_sigma
