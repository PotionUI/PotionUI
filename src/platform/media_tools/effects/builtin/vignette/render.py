from functools import lru_cache

import numpy as np


@lru_cache(maxsize=8)
def vignette_factor(width: int, height: int, depth: float, inner: float, outer: float) -> np.ndarray:
    dx = np.arange(width, dtype=np.float32) + np.float32(0.5) - np.float32(width / 2)
    dy = np.arange(height, dtype=np.float32) + np.float32(0.5) - np.float32(height / 2)
    d = np.hypot(dx[None, :], dy[:, None]) / np.float32(np.hypot(width / 2, height / 2))
    t = np.clip((d - np.float32(inner)) / np.float32(outer - inner), 0, 1)
    factor = np.float32(1) - np.float32(depth) * (t * t * (np.float32(3) - np.float32(2) * t))
    factor.setflags(write=False)
    return factor


def _factor(batch, derived) -> np.ndarray:
    height, width = batch.frames.shape[1:3]
    return vignette_factor(
        int(width), int(height), float(derived["depth"]), float(derived["inner"]), float(derived["outer"])
    )


def vignette_numpy(batch, params, derived):
    factor = _factor(batch, derived)[:, :, None]
    out = np.empty_like(batch.frames)
    for slot, frame in enumerate(batch.frames):
        out[slot] = np.floor(frame.astype(np.float32) * factor + np.float32(0.5))
    return out


def vignette_torch(batch, params, derived):
    import torch

    tensor = batch.tensor
    factor = torch.from_numpy(_factor(batch, derived)).to(device=tensor.device, dtype=tensor.dtype)
    values = torch.floor(tensor * 255 + 0.5)
    return torch.floor(values * factor + 0.5) / 255
