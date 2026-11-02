from __future__ import annotations

import numpy as np

from src.platform.media_tools.effects.api import FrameBatch, lowbias32

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
_U32 = np.uint32


def _frame_seed(seed: int, frame: int, channel: int) -> np.uint32:
    with np.errstate(over="ignore"):
        base = lowbias32(_U32(seed) ^ lowbias32(_U32(frame) + _U32(0x632BE5AB)))
        return lowbias32(base ^ (_U32(channel + 1) * _U32(0x27D4EB2D)))


def _noise(cells_x: int, cells_y: int, seed: np.uint32) -> np.ndarray:
    gx, gy = np.meshgrid(np.arange(cells_x, dtype=_U32), np.arange(cells_y, dtype=_U32))
    with np.errstate(over="ignore"):
        mixed = (gx * _U32(73856093)) ^ (gy * _U32(19349663)) ^ ((seed + _U32(1)) * _U32(83492791))
    h = lowbias32(mixed)
    second = lowbias32(h ^ _U32(0x9E3779B9))
    return (h.astype(np.float32) / 4294967296 + second.astype(np.float32) / 4294967296) - 1


def grain_numpy(batch: FrameBatch, params: dict, derived: dict) -> np.ndarray:
    frames = batch.frames
    _, height, width, _ = frames.shape
    cell = int(derived["cell"])
    cells_x, cells_y = -(-width // cell), -(-height // cell)
    channels = 3 if params["color"] else 1
    out = np.empty_like(frames)
    for slot, index in enumerate(batch.indices):
        px = frames[slot].astype(np.float32)
        t = 2 * (px @ LUMA) / 255 - 1
        mid = (1 - 0.7 * t * t)[:, :, None]
        layers = [_noise(cells_x, cells_y, _frame_seed(params["seed"], index, c)) for c in range(channels)]
        noise = np.stack(layers, axis=-1).repeat(cell, 0).repeat(cell, 1)[:height, :width]
        out[slot] = np.clip(np.floor(px + noise * derived["strength"] * mid + 0.5), 0, 255)
    return out


def _lowbias32_t(x):
    mask = 0xFFFFFFFF
    x = x ^ (x >> 16)
    x = (x * 0x7FEB352D) & mask
    x = x ^ (x >> 15)
    x = (x * 0x846CA68B) & mask
    return x ^ (x >> 16)


def grain_torch(batch, params: dict, derived: dict):
    import torch

    frames = batch.tensor
    n, _, height, width = frames.shape
    cell = int(derived["cell"])
    gy = torch.arange(-(-height // cell), device=frames.device, dtype=torch.int64)[:, None]
    gx = torch.arange(-(-width // cell), device=frames.device, dtype=torch.int64)[None, :]
    luma = (frames * torch.tensor(LUMA, device=frames.device)[None, :, None, None]).sum(1, keepdim=True)
    mid = 1 - 0.7 * (2 * luma - 1) ** 2
    out = torch.empty_like(frames)
    for slot, index in enumerate(batch.indices):
        layers = []
        for c in range(3 if params["color"] else 1):
            seed = int(_frame_seed(params["seed"], index, c))
            h = _lowbias32_t(((gx * 73856093) ^ (gy * 19349663) ^ (((seed + 1) * 83492791) & 0xFFFFFFFF)) & 0xFFFFFFFF)
            s = _lowbias32_t(h ^ 0x9E3779B9)
            layers.append(h.float() / 4294967296 + s.float() / 4294967296 - 1)
        noise = torch.stack(layers).repeat_interleave(cell, 1).repeat_interleave(cell, 2)[:, :height, :width]
        out[slot] = (frames[slot] + noise * derived["strength"] / 255 * mid[slot]).clamp(0, 1)
    return out
