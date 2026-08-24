# Derived from: diffusers `modular_pipelines/minimax_h3/decoders.py`
# (`MiniMaxH3AfterDenoiseStep`'s audio unpack, `MiniMaxH3AudioDecodeStep`),
# `before_encoder.py` (`MiniMaxH3Ref2VASetupStep._normalize_audio_condition`)
# and `encoders.py` (`MiniMaxH3Ref2VAReferenceEncoderStep`'s soundtrack
# branch), Apache-2.0, "Copyright 2026 The MiniMax and HuggingFace Teams".
"""MiniMax-H3 audio: channel-major row pack/unpack, waveform -> clean
condition rows, and VAE decode to an
:class:`~src.pipelines.pipes._shared.media.video_encode.AudioTrack`.

Stereo is carried as two CHANNEL-MAJOR row blocks in the packed sequence
(dossier §A.8 trap 5) and as two BATCH items at the (mono) audio VAE boundary
-- this module is the seam between the two representations.

The audio VAE's `encode` returns the posterior MEAN and draws no noise, so
:func:`encode_audio_condition` consumes nothing from the request generator and
does not disturb the pipe's "one generator, three draws, in order" contract.
That matches the reference, which takes `posterior.mode()` for a soundtrack
and comments that soundtracks "are never sampled" -- unlike a keyframe, whose
visual anchor IS sampled and IS noise-augmented to `t = 0.999`. Audio
condition rows stay clean (`t = 1.0`).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch

from src.pipelines.pipes._shared.media.video_encode import AudioTrack

Tensor = torch.Tensor

AUDIO_CHANNELS = 2

# The audio VAE's own rate (`vae.config.sampling_rate`); anything else has to
# be resampled onto it before encoding.
AUDIO_SAMPLE_RATE = 32000


def unpack_audio_rows(rows: Tensor, *, num_audio_latents: int, audio_channels: int = AUDIO_CHANNELS) -> Tensor:
    """`(audio_channels * num_audio_latents, latent_channels)` channel-major
    rows -> `(audio_channels, latent_channels, num_audio_latents)`, the mono
    audio VAE's per-channel-as-batch-item input shape."""
    rows = rows.reshape(audio_channels, num_audio_latents, rows.shape[-1])
    return rows.permute(0, 2, 1).contiguous()


def pack_audio_rows(latents: Tensor, *, audio_channels: int = AUDIO_CHANNELS) -> Tensor:
    """`(audio_channels, latent_channels, num_audio_latents)` -> the
    `(audio_channels * num_audio_latents, latent_channels)` channel-major rows
    the packed sequence carries -- the exact inverse of
    :func:`unpack_audio_rows`.

    This is the cheap path for chaining a generated window into the next one:
    the previous window's audio latents are already in this layout, so they can
    be re-packed directly instead of decoded to a waveform and re-encoded
    through :func:`encode_audio_condition` (which is lossy and needs the VAE).
    """
    if latents.ndim != 3 or latents.shape[0] != audio_channels:
        raise ValueError(
            f"expected ({audio_channels}, latent_channels, num_audio_latents) latents, got {tuple(latents.shape)}"
        )
    return latents.permute(0, 2, 1).reshape(-1, latents.shape[1]).contiguous()


def normalize_condition_waveform(
    waveform: Tensor, *, sample_rate: int, target_sample_rate: int = AUDIO_SAMPLE_RATE,
    max_duration: float | None = None, audio_channels: int = AUDIO_CHANNELS,
) -> Tensor:
    """A mono/stereo `(channels, samples)` waveform -> float32
    `(audio_channels, samples)` at `target_sample_rate`.

    Truncation is applied at the SOURCE rate and the resample is a single pass
    afterwards, which is the order the reference uses; a mono waveform is
    upmixed by repeating its channel. Resampling needs `torchaudio` (the
    reference's only resampler too) -- pass a waveform already at
    `target_sample_rate` to do without it.
    """
    waveform = torch.as_tensor(waveform)
    if waveform.ndim != 2 or waveform.shape[0] not in (1, audio_channels):
        raise ValueError(
            f"a condition waveform must be a (channels, samples) mono or {audio_channels}-channel tensor, "
            f"got {tuple(waveform.shape)}"
        )
    waveform = waveform.to(torch.float32)
    if max_duration is not None:
        waveform = waveform[:, : int(max_duration * sample_rate)]
    if waveform.shape[0] != audio_channels:
        waveform = waveform.expand(audio_channels, -1).contiguous()
    if sample_rate == target_sample_rate:
        return waveform

    try:
        import torchaudio
    except ImportError as error:
        raise ImportError(
            f"Resampling a MiniMax-H3 condition soundtrack from {sample_rate} Hz to {target_sample_rate} Hz "
            "needs `torchaudio`. Pass a waveform already at the audio VAE's sample rate to do without it."
        ) from error
    return torchaudio.transforms.Resample(sample_rate, target_sample_rate)(waveform)


def _fit_condition_latents(latents: Tensor, num_condition_audio_latents: int) -> Tensor:
    """Trim/pad `(channels, latent_channels, n)` to exactly
    `num_condition_audio_latents` frames, keeping the TAIL.

    The reference never does this -- a `ref2va` soundtrack's row count is
    derived from whatever the encoder produced, so the layout takes its length
    from the data. A fixed-length window has to reconcile the two, and the tail
    is the end that matters: it is the audio immediately preceding the target,
    and the layout places the condition block so that its last latent abuts
    `media_rotary_origin`. Short input is LEFT-padded with zeros, which in
    normalized latent space is the per-channel mean -- the most neutral filler
    available, and it lands on the older end where it is furthest from the
    target. Both conventions are derived, not read off the reference.
    """
    available = latents.shape[-1]
    if available == num_condition_audio_latents:
        return latents
    if available > num_condition_audio_latents:
        return latents[..., available - num_condition_audio_latents :]
    padding = torch.zeros(
        latents.shape[0], latents.shape[1], num_condition_audio_latents - available,
        dtype=latents.dtype, device=latents.device,
    )
    return torch.cat([padding, latents], dim=-1)


def encode_audio_condition(
    audio_vae_module: Any, waveform: Tensor, *, sample_rate: int,
    num_condition_audio_latents: int | None = None, max_duration: float | None = None,
    audio_channels: int = AUDIO_CHANNELS, device: Any = None, dtype: torch.dtype | None = None,
) -> Tensor:
    """Waveform -> the clean `(audio_channels * n, latent_channels)` condition
    rows `build_packed_sequence(..., num_condition_audio_latents=n)` reserves.

    Resamples/upmixes to `audio_channels` at the VAE's 32 kHz, encodes the
    channels as `audio_channels` BATCH items of the mono VAE, normalizes with
    the VAE's own `latents_mean`/`latents_std`, and packs channel-major.
    `num_condition_audio_latents` (default: whatever the audio encodes to)
    trims or pads to an exact count -- see :func:`_fit_condition_latents` for
    which end each does and why.

    Draws no noise: `MiniMaxH3AudioVAE.encode` returns the posterior mean.
    """
    waveform = normalize_condition_waveform(
        waveform, sample_rate=sample_rate, max_duration=max_duration, audio_channels=audio_channels,
    )
    if device is not None:
        waveform = waveform.to(device)

    with torch.no_grad():
        latents = audio_vae_module.encode(waveform[:, None])  # (channels, latent_channels, n), fp32

    latents_mean = audio_vae_module.latents_mean.to(device=latents.device, dtype=torch.float32).view(1, -1, 1)
    latents_std = audio_vae_module.latents_std.to(device=latents.device, dtype=torch.float32).view(1, -1, 1)
    latents = (latents.to(torch.float32) - latents_mean) / latents_std

    if num_condition_audio_latents is not None:
        latents = _fit_condition_latents(latents, num_condition_audio_latents)

    rows = pack_audio_rows(latents, audio_channels=audio_channels)
    return rows if dtype is None else rows.to(dtype)


def decode_generated_audio(
    audio_vae_module: Any, audio_rows: Tensor, *, num_audio_latents: int,
    num_condition_audio_rows: int = 0, sample_rate: int = AUDIO_SAMPLE_RATE,
) -> AudioTrack:
    """Denormalize + decode the generated audio rows into a stereo
    :class:`AudioTrack`.

    `audio_rows` is the whole audio stream; the leading
    `num_condition_audio_rows` conditioning rows are DROPPED before unpacking,
    exactly as the reference's own `audio_latents[num_condition_audio_rows:]`
    does -- only the generated tail is decoded.

    `audio_vae_module.latents_mean`/`.latents_std` are the real per-channel
    checkpoint tensors (see `vae/minimax_h3_audio.py`'s module docstring).
    The mono decoder takes the two stereo channels as two batch items
    (`(2, 1, samples)`); squeezing the mono axis gives `AudioTrack`'s own
    `(channels, samples)` convention directly.
    """
    device = audio_rows.device
    latents = unpack_audio_rows(audio_rows[num_condition_audio_rows:], num_audio_latents=num_audio_latents)
    latents_mean = audio_vae_module.latents_mean.to(device=device, dtype=torch.float32).view(1, -1, 1)
    latents_std = audio_vae_module.latents_std.to(device=device, dtype=torch.float32).view(1, -1, 1)
    latents = latents.to(torch.float32) * latents_std + latents_mean

    with torch.no_grad():
        waveform = audio_vae_module.decode(latents)  # (2, 1, samples), fp32, clamped [-1, 1]

    wf = waveform.detach().float().cpu().squeeze(1)  # (2, samples)
    return AudioTrack(waveform=np.clip(wf.numpy(), -1.0, 1.0).astype(np.float32), sample_rate=sample_rate)
