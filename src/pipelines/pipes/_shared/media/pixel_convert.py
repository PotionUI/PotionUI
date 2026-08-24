"""Chunked video-pixel -> uint8 frame conversion.

The naive idiom

    pixels = pixels.clamp(-1, 1).float()
    pixels = ((pixels + 1.0) * 127.5).round().to(torch.uint8)
    return pixels.permute(1, 2, 3, 0).contiguous().cpu().numpy()

allocates a fresh full-size tensor for EVERY op in the chain. On a long
high-resolution clip that is several ~6GB fp32 transients ON THE DECODE
DEVICE — a 10s 1056x1920 clip OOM'd a 32GB GPU on `(pixels + 1.0)` after
the actual VAE decode had already succeeded.
Converting per temporal chunk with in-place ops bounds the transient to
one chunk regardless of clip length, and writes straight into the final
(T, H, W, 3) uint8 numpy buffer.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import torch

# 32 frames at 1080p fp32 is ~0.8GB transient — comfortably small, and large
# enough that per-chunk overhead is noise next to the VAE decode itself.
_DEFAULT_CHUNK_FRAMES = 32

ValueRange = Literal["signed", "unit"]


def pixels_3thw_to_uint8_frames(
    pixels: torch.Tensor,
    chunk_frames: int = _DEFAULT_CHUNK_FRAMES,
    value_range: ValueRange = "signed",
) -> np.ndarray:
    """Convert a (3, T, H, W) tensor to (T, H, W, 3) uint8 frames.

    `value_range="signed"` (default) treats `pixels` as `[-1, 1]`; `"unit"`
    treats it as `[0, 1]` (e.g. MiniMax-H3's video VAE, which denormalizes to
    ImageNet pixel convention itself). Numerically identical to the naive
    full-tensor chain for each range (clamp -> shift -> scale -> round ->
    uint8): bf16/fp16 -> fp32 casting is exact, so clamping after the upcast
    instead of before it changes nothing. Works for `pixels` on any device;
    each converted chunk lands on CPU.
    """
    if pixels.ndim != 4 or pixels.shape[0] != 3:
        raise ValueError(f"expected a (3, T, H, W) tensor, got shape {tuple(pixels.shape)}")
    if value_range not in ("signed", "unit"):
        raise ValueError(f"expected value_range in ('signed', 'unit'), got {value_range!r}")

    _, t, h, w = pixels.shape
    out = np.empty((t, h, w, 3), dtype=np.uint8)
    step = max(1, int(chunk_frames))
    with torch.no_grad():
        for i in range(0, t, step):
            # copy=True guarantees a private chunk even when pixels is
            # already fp32, making the in-place ops below safe.
            chunk = pixels[:, i : i + step].to(dtype=torch.float32, copy=True)
            if value_range == "signed":
                chunk = chunk.clamp_(-1.0, 1.0).add_(1.0).mul_(127.5).round_().to(torch.uint8)
            else:
                chunk = chunk.clamp_(0.0, 1.0).mul_(255.0).round_().to(torch.uint8)
            out[i : i + step] = chunk.permute(1, 2, 3, 0).contiguous().cpu().numpy()
    return out
