# Derived from: https://github.com/aigc-apps/VideoX-Fun/blob/main/videox_fun/pipeline/pipeline_minimax_h3_control.py (Apache-2.0)
from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np
import torch
from torch import Tensor
from torch.nn import functional as F

from src.pipelines.pipes.generator.video_minimax_h3.conditioning import (
    PIXEL_MEAN,
    PIXEL_STD,
    fit_source_video,
    normalize_visual_latent,
)
from src.pipelines.pipes.generator.video_minimax_h3.geometry import FPS, FRAMES_PER_CHUNK, LATENTS_PER_CHUNK
from src.pipelines.pipes.generator.video_minimax_h3.layout import patchify_video_latents

HINT_ROW_MODES = ("trained", "non_audio")
MIN_CONTROL_FRAMES = LATENTS_PER_CHUNK


def available_frames(count: int, fps: float) -> int:
    if count < 1:
        raise ValueError("the control video has no frames")
    if fps <= 0:
        raise ValueError(f"the control video must have a positive frame rate, got {fps}")
    return int(math.floor(count * FPS / float(fps)))


def snap_control_frames(available: int, max_frames: int) -> int:
    snapped = max(MIN_CONTROL_FRAMES, (available - LATENTS_PER_CHUNK) // FRAMES_PER_CHUNK * FRAMES_PER_CHUNK + LATENTS_PER_CHUNK)
    return min(snapped, max_frames)


def fit_control_frames(frames: Any, *, fps: float, height: int, width: int, num_frames: int) -> np.ndarray:
    fitted = fit_source_video(frames, fps=fps, height=height, width=width)
    if len(fitted) >= num_frames:
        return fitted[:num_frames]
    tail = np.repeat(fitted[-1:], num_frames - len(fitted), axis=0)
    return np.concatenate([fitted, tail], axis=0)


def control_pixels(frames: np.ndarray, device: Any) -> Tensor:
    pixels = torch.from_numpy(frames.copy()).to(device).permute(3, 0, 1, 2)[None].contiguous().to(torch.float32)
    mean = torch.tensor(PIXEL_MEAN, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    std = torch.tensor(PIXEL_STD, device=device, dtype=torch.float32).view(1, -1, 1, 1, 1)
    return (pixels / 255.0 - mean) / std


def encode_control_rows(
    frames: np.ndarray, encoder: Callable[[Tensor], Tensor], *, patch_size: tuple[int, int, int],
    device: Any, latents_mean: Any, latents_std: Any, columns: int,
) -> Tensor:
    latent = encoder(control_pixels(frames, device))
    latent = normalize_visual_latent(latent, latents_mean=latents_mean, latents_std=latents_std)
    rows = patchify_video_latents(latent, patch_size).to(torch.float32)
    if columns < rows.shape[1]:
        raise ValueError(f"control_proj_in takes {columns} columns but the control rows are {rows.shape[1]} wide")
    if columns > rows.shape[1]:
        rows = F.pad(rows, (0, columns - rows.shape[1]))
    return rows[None]


def control_active(step_index: int, num_steps: int, start: float, end: float) -> bool:
    fraction = step_index / max(num_steps, 1)
    return start <= fraction < end


def validate_control_settings(scale: float, start: float, end: float, hint_rows: str) -> None:
    if scale < 0:
        raise ValueError(f"control_context_scale must not be negative, got {scale}")
    if not 0.0 <= start < end <= 1.0:
        raise ValueError(f"control_start/control_end must satisfy 0 <= start < end <= 1, got {start} and {end}")
    if hint_rows not in HINT_ROW_MODES:
        raise ValueError(f"control_hint_rows must be one of {HINT_ROW_MODES}, got {hint_rows!r}")


def control_reserve_gb(seq_len: int, hidden_size: int, bytes_per_element: int = 2) -> float:
    return 3 * seq_len * hidden_size * bytes_per_element / (1024 ** 3)
