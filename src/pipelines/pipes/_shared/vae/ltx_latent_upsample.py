"""The LTX latent-upsample recipe: un-normalize -> forward -> re-normalize.

Shared by ``latent_upscaler/ltx`` (one spatial or temporal pass per pipeline)
and ``generator/dfr_video_ltx`` (one temporal pass per densification round, so
the recipe runs repeatedly inside a single pipe).

The normalization sandwich is not optional and its omission does not crash:
the ``LTXLatentUpsampler`` arch operates on UN-normalized latents, so skipping
the video VAE's own ``per_channel_statistics`` round trip produces a scale
mismatch that reads as washed-out or blown-out output rather than as a bug.
Because the statistics used here are the same ones the diffusion sampling loop
(de)normalizes against, the result feeds straight back into a denoise call with
no extra scale/shift bookkeeping at the call site.

Frame-count contract for the temporal upsampler: the arch pixel-shuffles the
frame axis by 2 and then drops the first output frame (that frame encodes a
single pixel frame under causal encoding, so doubling it is meaningless), i.e.
``T -> 2T - 1`` latent frames --
``latent_upscaler/ltx/geometry.temporal_upsample_out_frames``. The drop lives
inside the arch; callers must not re-apply it.
"""

from __future__ import annotations

from typing import Any

import torch


def upsample_ltx_latent(bundle: Any, upsampler: Any, latent: torch.Tensor, device: str) -> torch.Tensor:
    """Run ``upsampler`` over ``latent`` inside the VAE's normalization sandwich.

    ``upsampler`` is a loaded ``NativeModel`` holding either bundle upsampler
    slot -- the recipe is identical either way, only the checkpoint differs.
    Both the VAE and the upsampler are moved onto ``device`` for the call and
    offloaded again on the way out (including on an exception).
    """
    bundle.vae.move_to(device)
    upsampler.move_to(device)
    try:
        with torch.no_grad():
            z = latent.to(device=device, dtype=upsampler.compute_dtype)
            z = bundle.vae.module.per_channel_statistics.un_normalize(z)
            z = upsampler.module(z)
            z = bundle.vae.module.per_channel_statistics.normalize(z)
    finally:
        upsampler.offload()
        bundle.vae.offload()
    return z
