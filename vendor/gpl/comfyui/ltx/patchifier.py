# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/lightricks/symmetric_patchifier.py @ unknown;
# vendored ~2025 (moved into vendor/gpl/comfyui/ltx/ from
# src/platform/runtime/native/arch/ltx/ as part of the license-relocation
# workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.

"""Patchifiers + latent->pixel coordinate mapping for the LTX-2 AV forward.

Vendored verbatim (behaviour-preserving) from ComfyUI
``comfy/ldm/lightricks/symmetric_patchifier.py``. Stateless helpers — no
registered buffers, so they play no part in ``post_load``.

  * ``SymmetricPatchifier`` turns a video latent ``(B, C, F, H, W)`` into a token
    sequence ``(B, F*H*W, C*p1*p2*p3)`` plus per-token latent corner coordinates,
    and inverts it.
  * ``AudioPatchifier`` turns an audio latent ``(B, C, T, freq)`` into
    ``(B, T, C*freq)`` plus per-token *time-in-seconds* coordinates (causal mel
    downsample aware), and inverts it.
  * ``latent_to_pixel_coords`` scales latent coordinates by the VAE downscale
    factors, with the LTX causal first-frame temporal correction.

The default LTX patch size is ``1`` (no spatial patch merge): both patchifiers
are built with ``patch_size=1, start_end=True`` so each token carries a
``(start, end)`` coordinate pair used by the fractional-position RoPE grid.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import torch
from einops import rearrange
from torch import Tensor


def latent_to_pixel_coords(latent_coords: Tensor, scale_factors, causal_fix: bool = False) -> Tensor:
    """Scale latent corner coordinates to pixel coordinates (ComfyUI parity).

    ``latent_coords`` is ``(B, 3, num_latents[, 2])``; ``scale_factors`` is the
    VAE ``(t, h, w)`` downscale. With ``causal_fix`` the first-frame temporal
    coordinate is corrected for the causal VAE (frame 0 spans one pixel-frame).
    """
    shape = [1] * latent_coords.ndim
    shape[1] = -1
    pixel_coords = latent_coords * torch.tensor(scale_factors, device=latent_coords.device).view(*shape)
    if causal_fix:
        pixel_coords[:, 0, ...] = (pixel_coords[:, 0, ...] + 1 - scale_factors[0]).clamp(min=0)
    return pixel_coords


class Patchifier(ABC):
    def __init__(self, patch_size: int, start_end: bool = False):
        super().__init__()
        self._patch_size = (1, patch_size, patch_size)
        self.start_end = start_end

    @abstractmethod
    def patchify(self, latents: Tensor):
        ...

    @abstractmethod
    def unpatchify(self, latents: Tensor, **kwargs):
        ...

    @property
    def patch_size(self):
        return self._patch_size

    def get_latent_coords(self, latent_num_frames, latent_height, latent_width, batch_size, device):
        """``(B, 3, num_patches[, 2])`` top-left (and, when ``start_end``, bottom-right)
        latent coordinates of each patch, repeated over the batch."""
        latent_sample_coords = torch.meshgrid(
            torch.arange(0, latent_num_frames, self._patch_size[0], device=device),
            torch.arange(0, latent_height, self._patch_size[1], device=device),
            torch.arange(0, latent_width, self._patch_size[2], device=device),
            indexing="ij",
        )
        latent_sample_coords_start = torch.stack(latent_sample_coords, dim=0)
        delta = torch.tensor(self._patch_size, device=latent_sample_coords_start.device,
                             dtype=latent_sample_coords_start.dtype)[:, None, None, None]
        latent_sample_coords_end = latent_sample_coords_start + delta

        latent_sample_coords_start = latent_sample_coords_start.unsqueeze(0).repeat(batch_size, 1, 1, 1, 1)
        latent_sample_coords_start = rearrange(latent_sample_coords_start, "b c f h w -> b c (f h w)", b=batch_size)
        if self.start_end:
            latent_sample_coords_end = latent_sample_coords_end.unsqueeze(0).repeat(batch_size, 1, 1, 1, 1)
            latent_sample_coords_end = rearrange(latent_sample_coords_end, "b c f h w -> b c (f h w)", b=batch_size)
            latent_coords = torch.stack((latent_sample_coords_start, latent_sample_coords_end), dim=-1)
        else:
            latent_coords = latent_sample_coords_start
        return latent_coords


class SymmetricPatchifier(Patchifier):
    def patchify(self, latents: Tensor):
        b, _, f, h, w = latents.shape
        latent_coords = self.get_latent_coords(f, h, w, b, latents.device)
        latents = rearrange(
            latents,
            "b c (f p1) (h p2) (w p3) -> b (f h w) (c p1 p2 p3)",
            p1=self._patch_size[0], p2=self._patch_size[1], p3=self._patch_size[2],
        )
        return latents, latent_coords

    def unpatchify(self, latents: Tensor, output_height: int, output_width: int,
                   output_num_frames: int, out_channels: int) -> Tensor:
        output_height = output_height // self._patch_size[1]
        output_width = output_width // self._patch_size[2]
        return rearrange(
            latents,
            "b (f h w) (c p q) -> b c f (h p) (w q) ",
            f=output_num_frames, h=output_height, w=output_width,
            p=self._patch_size[1], q=self._patch_size[2],
        )


class AudioPatchifier(Patchifier):
    def __init__(self, patch_size: int, sample_rate=16000, hop_length=160,
                 audio_latent_downsample_factor=4, is_causal=True, start_end=False, shift=0):
        super().__init__(patch_size, start_end=start_end)
        self.hop_length = hop_length
        self.sample_rate = sample_rate
        self.audio_latent_downsample_factor = audio_latent_downsample_factor
        self.is_causal = is_causal
        self.shift = shift

    def _get_audio_latent_time_in_sec(self, start_latent, end_latent: int, dtype: torch.dtype, device):
        audio_latent_frame = torch.arange(start_latent, end_latent, dtype=dtype, device=device)
        audio_mel_frame = audio_latent_frame * self.audio_latent_downsample_factor
        if self.is_causal:
            audio_mel_frame = (audio_mel_frame + 1 - self.audio_latent_downsample_factor).clip(min=0)
        return audio_mel_frame * self.hop_length / self.sample_rate

    def patchify(self, audio_latents: Tensor):
        # audio_latents: (batch, channels, time, freq)
        b, _, t, _ = audio_latents.shape
        audio_latents = rearrange(audio_latents, "b c t f -> b t (c f)")

        start = self._get_audio_latent_time_in_sec(self.shift, t + self.shift, torch.float32, audio_latents.device)
        start = start.unsqueeze(0).expand(b, -1).unsqueeze(1)
        if self.start_end:
            end = self._get_audio_latent_time_in_sec(self.shift + 1, t + self.shift + 1, torch.float32, audio_latents.device)
            end = end.unsqueeze(0).expand(b, -1).unsqueeze(1)
            timings = torch.stack([start, end], dim=-1)
        else:
            timings = start
        return audio_latents, timings

    def unpatchify(self, audio_latents: Tensor, channels: int, freq: int) -> Tensor:
        # audio_latents: (batch, time, freq * channels)
        return rearrange(audio_latents, "b t (c f) -> b c t f", c=channels, f=freq)
