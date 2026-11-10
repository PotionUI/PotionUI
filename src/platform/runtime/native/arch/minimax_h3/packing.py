# Derived from: diffusers `modular_pipelines/minimax_h3/before_denoise.py` (Apache-2.0, "Copyright 2026 The MiniMax and HuggingFace Teams") -- `patchify_video_latents`, `_spatial_position_grid` and `_frame_position_grid`.
from __future__ import annotations

import numpy as np
import torch

Tensor = torch.Tensor

ROPE_SPATIAL_SCALE = 32

VIDEO_TAG = 0
TEXT_TAG = 1
AUDIO_TAG = 2


def patchify_video_latents(latents: Tensor, patch_size: tuple[int, int, int]) -> Tensor:
    patch_t, patch_h, patch_w = patch_size
    b, c, f, h, w = latents.shape
    if f % patch_t or h % patch_h or w % patch_w:
        raise ValueError(f"latents of shape {tuple(latents.shape)} are not divisible by patch {patch_size}")
    latents = latents.reshape(b, c, f // patch_t, patch_t, h // patch_h, patch_h, w // patch_w, patch_w)
    latents = latents.permute(0, 2, 4, 6, 1, 3, 5, 7)
    return latents.reshape(-1, c * patch_t * patch_h * patch_w).contiguous()


def unpatchify_video_rows(
    rows: Tensor, *, num_latent_frames: int, latent_height: int, latent_width: int,
    channels: int, patch_size: tuple[int, int, int],
) -> Tensor:
    patch_t, patch_h, patch_w = patch_size
    rows = rows.reshape(
        -1, num_latent_frames // patch_t, latent_height // patch_h, latent_width // patch_w,
        channels, patch_t, patch_h, patch_w,
    )
    rows = rows.permute(0, 4, 1, 5, 2, 6, 3, 7)
    return rows.reshape(-1, channels, num_latent_frames, latent_height, latent_width).contiguous()


def spatial_position_grid(dim: int, patch: int, sqrt_area: float, device: torch.device) -> Tensor:
    ratio = dim / sqrt_area
    left = (1.0 - ratio) / 2.0
    grid = np.linspace(left, left + ratio, dim // patch, endpoint=False) * ROPE_SPATIAL_SCALE
    return torch.from_numpy(grid).to(device=device, dtype=torch.float64)


def frame_position_grid(
    latent_height: int, latent_width: int, patch_h: int, patch_w: int, device: torch.device,
) -> tuple[Tensor, Tensor]:
    sqrt_area = np.sqrt(latent_height * latent_width)
    height_grid = spatial_position_grid(latent_height, patch_h, sqrt_area, device)
    width_grid = spatial_position_grid(latent_width, patch_w, sqrt_area, device)
    grids = torch.meshgrid(height_grid, width_grid, indexing="ij")
    return torch.stack([grid.reshape(-1) for grid in grids], dim=-1), width_grid
