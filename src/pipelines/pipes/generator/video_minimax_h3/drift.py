from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_FRAME_STRIDE = 4
SAMPLE_PIXEL_STRIDE = 4
MIN_SCALE = 0.8
MAX_SCALE = 1.25
CHUNK_FRAMES = 16


@dataclass(frozen=True)
class ColourStats:
    mean: np.ndarray
    std: np.ndarray


def colour_stats(frames: np.ndarray) -> ColourStats:
    sample = frames[::SAMPLE_FRAME_STRIDE, ::SAMPLE_PIXEL_STRIDE, ::SAMPLE_PIXEL_STRIDE].astype(np.float64)
    flat = sample.reshape(-1, sample.shape[-1])
    return ColourStats(mean=flat.mean(axis=0), std=flat.std(axis=0))


def correct_drift(frames: np.ndarray, anchor: ColourStats, strength: float) -> np.ndarray:
    if strength <= 0.0 or frames.shape[0] == 0:
        return frames
    current = colour_stats(frames)
    scale = np.clip(anchor.std / np.maximum(current.std, 1e-6), MIN_SCALE, MAX_SCALE)
    shift = anchor.mean - current.mean * scale
    gain = 1.0 + strength * (scale - 1.0)
    offset = strength * shift
    out = np.empty_like(frames)
    for start in range(0, frames.shape[0], CHUNK_FRAMES):
        chunk = frames[start:start + CHUNK_FRAMES].astype(np.float32)
        out[start:start + CHUNK_FRAMES] = np.clip(chunk * gain.astype(np.float32) + offset.astype(np.float32), 0, 255).astype(frames.dtype)
    return out


class DriftGuard:
    def __init__(self, strength: float) -> None:
        self._strength = float(strength)
        self._anchor: ColourStats | None = None

    def apply(self, frames: np.ndarray, *, continues_previous: bool) -> np.ndarray:
        if self._strength <= 0.0 or frames.shape[0] == 0:
            return frames
        if self._anchor is None or not continues_previous:
            self._anchor = colour_stats(frames)
            return frames
        return correct_drift(frames, self._anchor, self._strength)
