"""Tests for the MiniMax-H3 audio row pack/unpack and condition encoder.

CPU-only, no weights: the audio VAE is stood in for by a fake with the real
`MiniMaxH3AudioVAE`'s exact `encode` contract -- `(B, 1, samples)` in,
`(B, latent_channels, samples // hop_length)` out, right-padded to a hop
multiple, fp32, deterministic (the real one returns the posterior MEAN and
never draws noise). The channel-major row convention is re-derived
INDEPENDENTLY here from the reference's own formula
(`normalized.reshape(-1, audio_latent_channels)` over a `(channels, n,
latent_channels)` tensor) rather than by calling the module under test, so a
bug shared by pack and unpack cannot hide.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.audio import (
    AUDIO_SAMPLE_RATE,
    decode_generated_audio,
    encode_audio_condition,
    normalize_condition_waveform,
    pack_audio_rows,
    unpack_audio_rows,
)

LATENT_CHANNELS = 32
HOP_LENGTH = 800


class _FakeAudioVae:
    """The real VAE's encode/decode shape contract, with arithmetic simple
    enough to predict exactly."""

    def __init__(self, latent_channels: int = LATENT_CHANNELS, hop_length: int = HOP_LENGTH):
        self.latent_channels = latent_channels
        self.hop_length = hop_length
        self.latents_mean = torch.arange(latent_channels, dtype=torch.float32)
        self.latents_std = torch.arange(1, latent_channels + 1, dtype=torch.float32)
        self.encode_calls: list[tuple[int, ...]] = []

    def encode(self, sample: torch.Tensor) -> torch.Tensor:
        assert sample.ndim == 3 and sample.shape[1] == 1, tuple(sample.shape)
        self.encode_calls.append(tuple(sample.shape))
        sample = sample.float()
        pad = math.ceil(sample.shape[-1] / self.hop_length) * self.hop_length - sample.shape[-1]
        if pad:
            sample = torch.nn.functional.pad(sample, (0, pad))
        frames = sample.reshape(sample.shape[0], 1, -1, self.hop_length).mean(-1)
        return frames.expand(-1, self.latent_channels, -1).contiguous()

    def decode(self, latents: torch.Tensor) -> torch.Tensor:
        return latents.mean(1, keepdim=True).repeat_interleave(self.hop_length, dim=-1)


def _reference_rows(latents: torch.Tensor) -> torch.Tensor:
    """`(channels, latent_channels, n)` -> channel-major rows, written out the
    way the reference does it (transpose to `(channels, n, latent_channels)`
    then flatten the leading two axes)."""
    channels, latent_channels, num = latents.shape
    out = torch.empty(channels * num, latent_channels, dtype=latents.dtype)
    for channel in range(channels):
        for index in range(num):
            out[channel * num + index] = latents[channel, :, index]
    return out


# -- pack / unpack -------------------------------------------------------------

def test_pack_matches_the_independently_written_channel_major_order():
    latents = torch.randn(2, LATENT_CHANNELS, 7, dtype=torch.float64)
    assert torch.equal(pack_audio_rows(latents), _reference_rows(latents))


def test_pack_unpack_roundtrip_is_bit_exact():
    latents = torch.randn(2, LATENT_CHANNELS, 11, dtype=torch.float64)
    rows = pack_audio_rows(latents)
    assert rows.shape == (2 * 11, LATENT_CHANNELS)
    back = unpack_audio_rows(rows, num_audio_latents=11)
    assert torch.equal(back, latents)


def test_unpack_pack_roundtrip_is_bit_exact():
    rows = torch.randn(2 * 9, LATENT_CHANNELS, dtype=torch.float64)
    back = pack_audio_rows(unpack_audio_rows(rows, num_audio_latents=9))
    assert torch.equal(back, rows)


def test_bite_check_pack_is_channel_major_not_latent_major():
    # BITE CHECK for the pack/unpack inverse: a latent-MAJOR pack (the obvious
    # wrong transpose -- rows interleaved L,R,L,R instead of blocked L...L,
    # R...R) produces the same SHAPE and survives its own inverse, so shape
    # and roundtrip alone prove nothing. It must differ from what pack emits,
    # and it must break unpack.
    latents = torch.randn(2, LATENT_CHANNELS, 6, dtype=torch.float64)
    latent_major = latents.permute(2, 0, 1).reshape(-1, LATENT_CHANNELS)
    correct = pack_audio_rows(latents)
    assert latent_major.shape == correct.shape
    assert not torch.equal(latent_major, correct)
    assert not torch.equal(unpack_audio_rows(latent_major, num_audio_latents=6), latents)


def test_pack_rejects_a_wrong_channel_count():
    with pytest.raises(ValueError):
        pack_audio_rows(torch.randn(3, LATENT_CHANNELS, 4))


def test_pack_accepts_a_non_contiguous_input():
    latents = torch.randn(2, LATENT_CHANNELS, 5, dtype=torch.float64).transpose(1, 2).transpose(1, 2)
    assert torch.equal(pack_audio_rows(latents), _reference_rows(latents))


# -- waveform normalization ----------------------------------------------------

def test_mono_is_upmixed_by_repeating_its_channel():
    waveform = torch.randn(1, 1000)
    out = normalize_condition_waveform(waveform, sample_rate=AUDIO_SAMPLE_RATE)
    assert out.shape == (2, 1000)
    assert torch.equal(out[0], out[1])
    assert out.dtype == torch.float32


def test_stereo_passes_through_at_the_target_rate():
    waveform = torch.randn(2, 500, dtype=torch.float64)
    out = normalize_condition_waveform(waveform, sample_rate=AUDIO_SAMPLE_RATE)
    assert out.dtype == torch.float32
    assert torch.equal(out, waveform.float())


def test_truncation_is_applied_at_the_source_rate():
    # max_duration multiplies the SOURCE rate, before any resample -- the
    # order the reference uses.
    waveform = torch.randn(2, 8000)
    out = normalize_condition_waveform(waveform, sample_rate=16000, target_sample_rate=16000, max_duration=0.25)
    assert out.shape == (2, 4000)


def test_a_wrongly_shaped_waveform_is_rejected():
    with pytest.raises(ValueError):
        normalize_condition_waveform(torch.randn(4, 100), sample_rate=AUDIO_SAMPLE_RATE)
    with pytest.raises(ValueError):
        normalize_condition_waveform(torch.randn(100), sample_rate=AUDIO_SAMPLE_RATE)


def test_a_matching_rate_skips_the_resampler_entirely():
    # Bit-exact passthrough, so a same-rate waveform never eats a resampling
    # pass it does not need.
    waveform = torch.randn(2, 777)
    out = normalize_condition_waveform(waveform, sample_rate=AUDIO_SAMPLE_RATE)
    assert torch.equal(out, waveform)


def test_a_different_rate_is_resampled_onto_the_vae_rate():
    torchaudio = pytest.importorskip("torchaudio")
    waveform = torch.randn(2, 16000)
    out = normalize_condition_waveform(waveform, sample_rate=16000)
    assert out.shape == (2, 32000)
    expected = torchaudio.transforms.Resample(16000, AUDIO_SAMPLE_RATE)(waveform.float())
    assert torch.equal(out, expected)


def test_encode_resamples_before_handing_the_waveform_to_the_vae():
    pytest.importorskip("torchaudio")
    vae = _FakeAudioVae()
    # 1 s at 16 kHz -> 32000 samples at the VAE's rate -> 40 latents.
    encode_audio_condition(vae, torch.randn(2, 16000), sample_rate=16000)
    assert vae.encode_calls == [(2, 1, 32000)]


# -- encode_audio_condition ----------------------------------------------------

def test_encode_produces_rows_the_layout_reserves_and_normalizes_with_the_vae_stats():
    vae = _FakeAudioVae()
    num_latents = 5
    waveform = torch.randn(2, num_latents * HOP_LENGTH)
    rows = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE)

    assert rows.shape == (2 * num_latents, LATENT_CHANNELS)
    assert vae.encode_calls == [(2, 1, num_latents * HOP_LENGTH)]

    raw = vae.encode(waveform[:, None])
    expected = (raw - vae.latents_mean.view(1, -1, 1)) / vae.latents_std.view(1, -1, 1)
    assert torch.equal(unpack_audio_rows(rows, num_audio_latents=num_latents), expected)


def test_encode_output_unpacks_back_to_per_channel_latents():
    vae = _FakeAudioVae()
    left = torch.randn(1, 4 * HOP_LENGTH)
    right = torch.randn(1, 4 * HOP_LENGTH)
    rows = encode_audio_condition(vae, torch.cat([left, right]), sample_rate=AUDIO_SAMPLE_RATE)
    latents = unpack_audio_rows(rows, num_audio_latents=4)
    assert latents.shape == (2, LATENT_CHANNELS, 4)
    # Channel 0 of the unpacked latents must come from the left waveform only.
    left_only = encode_audio_condition(vae, left.expand(2, -1), sample_rate=AUDIO_SAMPLE_RATE)
    assert torch.equal(latents[0], unpack_audio_rows(left_only, num_audio_latents=4)[0])


def test_encode_draws_no_noise_so_it_is_reproducible():
    vae = _FakeAudioVae()
    waveform = torch.randn(2, 3 * HOP_LENGTH)
    first = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE)
    second = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE)
    assert torch.equal(first, second)


def test_encode_trims_long_audio_from_the_front_keeping_the_tail():
    vae = _FakeAudioVae()
    waveform = torch.randn(2, 10 * HOP_LENGTH)
    full = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE)
    trimmed = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE, num_condition_audio_latents=4)

    assert trimmed.shape == (2 * 4, LATENT_CHANNELS)
    kept = unpack_audio_rows(trimmed, num_audio_latents=4)
    assert torch.equal(kept, unpack_audio_rows(full, num_audio_latents=10)[..., -4:])


def test_encode_left_pads_short_audio_with_the_channel_mean():
    vae = _FakeAudioVae()
    waveform = torch.randn(2, 2 * HOP_LENGTH)
    padded = encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE, num_condition_audio_latents=5)
    latents = unpack_audio_rows(padded, num_audio_latents=5)

    assert latents.shape == (2, LATENT_CHANNELS, 5)
    # Zero in NORMALIZED space is the per-channel mean, and it lands on the
    # older end so the real audio still abuts the target.
    assert torch.all(latents[..., :3] == 0.0)
    unpadded = unpack_audio_rows(
        encode_audio_condition(vae, waveform, sample_rate=AUDIO_SAMPLE_RATE), num_audio_latents=2
    )
    assert torch.equal(latents[..., 3:], unpadded)


def test_encode_casts_to_the_requested_dtype():
    vae = _FakeAudioVae()
    rows = encode_audio_condition(
        vae, torch.randn(2, 2 * HOP_LENGTH), sample_rate=AUDIO_SAMPLE_RATE, dtype=torch.bfloat16
    )
    assert rows.dtype == torch.bfloat16


def test_encode_upmixes_a_mono_condition_to_two_identical_channels():
    vae = _FakeAudioVae()
    rows = encode_audio_condition(vae, torch.randn(1, 3 * HOP_LENGTH), sample_rate=AUDIO_SAMPLE_RATE)
    latents = unpack_audio_rows(rows, num_audio_latents=3)
    assert torch.equal(latents[0], latents[1])


# -- decode drops the condition prefix ----------------------------------------

def test_decode_drops_the_leading_condition_audio_rows():
    vae = _FakeAudioVae()
    condition = torch.randn(2 * 3, LATENT_CHANNELS)
    generated = torch.randn(2 * 4, LATENT_CHANNELS)

    with_condition = decode_generated_audio(
        vae, torch.cat([condition, generated]), num_audio_latents=4, num_condition_audio_rows=2 * 3,
    )
    alone = decode_generated_audio(vae, generated, num_audio_latents=4)

    assert with_condition.waveform.shape == alone.waveform.shape
    assert (with_condition.waveform == alone.waveform).all()
    assert with_condition.sample_rate == AUDIO_SAMPLE_RATE
