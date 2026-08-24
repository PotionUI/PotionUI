"""Audio helpers for ``generator/video_ltx``: token geometry, unpack, decode.

The sampled audio slice of the packed state is decoded through the bundle's
audio VAE + vocoder into an :class:`~src.pipelines.pipes._shared.media.video_encode
.AudioTrack`, which ``encode_frames_to_mp4(..., audio=...)`` muxes in the same
encode pass (user-supplied audio files pass through as plain paths).
"""

from __future__ import annotations

import numpy as np
import torch

from src.platform.runtime.native.vae.ltx_audio import decode_audio_waveform
from src.pipelines.contracts import logger
from src.pipelines.pipes._shared.media.video_encode import AudioTrack

# Fixed LTX audio-latent geometry (AudioPatchifier defaults: 16 kHz mel at hop
# 160, causally downsampled 4x -> 25 latent tokens per second of video).
LTX_AUDIO_TOKENS_PER_SECOND = 25.0
AUDIO_CHANNELS = 8
AUDIO_MEL_BINS = 16


def audio_token_count(frames: int, fps: float) -> int:
    """Audio latent length for a clip: ``round(duration_s * 25)``, minimum 1."""
    return max(1, round(frames / fps * LTX_AUDIO_TOKENS_PER_SECOND))


def unpack_audio_tokens(tokens: torch.Tensor) -> torch.Tensor:
    """``[B, T_a, 128]`` packed state slice -> ``[B, 8, T_a, 16]`` audio latent
    (inverse of ``AudioPatchifier.patchify``'s ``b c t f -> b t (c f)``)."""
    b, t, _ = tokens.shape
    return tokens.view(b, t, AUDIO_CHANNELS, AUDIO_MEL_BINS).permute(0, 2, 1, 3).contiguous()


def decode_generated_audio(bundle, audio_tokens: torch.Tensor, device: str) -> AudioTrack:
    """Decode sampled audio tokens (``[1, T_a, 128]``) through the bundle's
    audio VAE + vocoder into an :class:`AudioTrack` for mux-at-encode."""
    latents = unpack_audio_tokens(audio_tokens)
    bundle.audio_vae.move_to(device)
    bundle.vocoder.move_to(device)
    try:
        with torch.no_grad():
            waveform, sample_rate = decode_audio_waveform(
                bundle.audio_vae.module, bundle.vocoder.module,
                latents.to(device=device, dtype=bundle.audio_vae.compute_dtype),
            )
    finally:
        bundle.audio_vae.offload()
        bundle.vocoder.offload()

    wf = waveform.detach().float().cpu()
    while wf.ndim > 2:
        wf = wf[0]
    if wf.ndim == 1:
        wf = wf.unsqueeze(0)
    track = AudioTrack(
        waveform=np.clip(wf.numpy(), -1.0, 1.0).astype(np.float32),
        sample_rate=int(sample_rate),
    )
    logger.debug("[GENERATOR VIDEO-LTX] decoded generated audio: %d ch, %d Hz, %.2fs",
                track.waveform.shape[0], track.sample_rate,
                track.waveform.shape[1] / track.sample_rate)
    return track
