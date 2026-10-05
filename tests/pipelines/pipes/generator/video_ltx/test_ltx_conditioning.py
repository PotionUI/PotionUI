"""Math tests for the LTX media-conditioning builder — every rule re-derived
from the diffusers v0.39 ltx2 condition/ic-lora pipelines: first-frame token
overwrite, appended keyframe coords (no causal fix, pixel-frame offset,
single-frame clamp, NO fps division), reference coords (causal fix, origin 0),
the trim formula, and the per-token initial noise mix."""

from __future__ import annotations

import pytest
import torch

from src.pipelines.pipes.generator.video_ltx.conditioning import (
    LTXMediaCondition,
    _temporal_subsample,
    _trim_condition_frames,
    mix_initial_noise,
    prepare_ltx_conditions,
)

# Geometry used throughout: 17 pixel frames -> t_lat 3; 64x64 px -> 2x2 latent.
FRAMES, H, W = 17, 64, 64
T_LAT, H_LAT, W_LAT = 3, 2, 2
S_BASE = T_LAT * H_LAT * W_LAT  # 12
C = 4  # tiny latent channel count for tests


def _fake_encode(fill: float):
    """Shape-honouring fake of LTXCausalVideoVAE.encode: (1,3,T,H,W) ->
    (1, C, (T-1)//8+1, H//32, W//32) filled with `fill`."""

    def enc(pixels):
        _, _, t, h, w = pixels.shape
        return torch.full((1, C, (t - 1) // 8 + 1, h // 32, w // 32), float(fill))

    return enc


def _frames(n=1, h=H, w=W):
    return torch.rand(n, h, w, 3)


def _prepare(conditions, fill=3.0):
    return prepare_ltx_conditions(
        conditions, _fake_encode(fill), frames=FRAMES, height=H, width=W,
        device="cpu", dtype=torch.float32, latent_channels=C,
    )


# -- first-frame overwrite -----------------------------------------------------

def test_first_frame_overwrites_leading_tokens():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=0, strength=1.0)], fill=5.0)
    n_tok = 1 * H_LAT * W_LAT  # one latent frame
    assert p.n_extra == 0 and p.tokens.shape == (1, S_BASE, C)
    assert torch.all(p.tokens[:, :n_tok] == 5.0)
    assert torch.all(p.tokens[:, n_tok:] == 0.0)
    assert torch.all(p.mask[:, :n_tok] == 1.0) and torch.all(p.mask[:, n_tok:] == 0.0)
    assert torch.equal(p.clean, p.tokens) and p.clean.data_ptr() != p.tokens.data_ptr()


def test_first_frame_strength_lands_in_mask():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=0, strength=0.6)])
    assert torch.allclose(p.mask[:, : H_LAT * W_LAT], torch.full((1, H_LAT * W_LAT), 0.6))


# -- appended keyframes ---------------------------------------------------------

def test_keyframe_appends_tokens_with_offset_coords_no_causal_fix():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=1, strength=0.8)], fill=2.0)
    n_kf = 1 * H_LAT * W_LAT
    assert p.n_extra == n_kf
    assert p.tokens.shape == (1, S_BASE + n_kf, C)
    assert torch.all(p.tokens[:, :S_BASE] == 0.0)          # base untouched
    assert torch.all(p.tokens[:, S_BASE:] == 2.0)          # appended clean values
    assert torch.allclose(p.mask[:, S_BASE:], torch.full_like(p.mask[:, S_BASE:], 0.8))
    # Coords: [1, 3, n_kf, 2]; temporal start = (1-1)*8 + 1 = 1 (pixel frames,
    # NOT divided by fps), single-pixel-frame clamp -> end = start + 1 = 2.
    assert p.extra_coords.shape == (1, 3, n_kf, 2)
    assert torch.all(p.extra_coords[:, 0, :, 0] == 1.0)
    assert torch.all(p.extra_coords[:, 0, :, 1] == 2.0)
    # Spatial coords stay VAE-scaled pixel units (0/32 starts, 32/64 ends).
    assert set(p.extra_coords[0, 1, :, 0].tolist()) == {0.0, 32.0}
    assert set(p.extra_coords[0, 1, :, 1].tolist()) == {32.0, 64.0}


def test_keyframe_multi_frame_clip_keeps_vae_scaled_temporal_extent():
    # 9 input frames at latent_index 1 -> trimmed to 9 (1+8k), latent frames 2.
    p = _prepare([LTXMediaCondition(frames=_frames(9), latent_index=1)])
    n_kf = 2 * H_LAT * W_LAT
    assert p.n_extra == n_kf
    starts = p.extra_coords[0, 0, :, 0]
    ends = p.extra_coords[0, 0, :, 1]
    # No single-frame clamp: temporal spans stay 8-wide, offset by 1.
    assert set(starts.tolist()) == {1.0, 9.0}
    assert torch.all(ends - starts == 8.0)


def test_negative_latent_index_resolves_from_end():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=-1)])
    # -1 % 3 = 2 -> pixel_frame_idx = (2-1)*8+1 = 9
    assert torch.all(p.extra_coords[:, 0, :, 0] == 9.0)


def test_out_of_range_latent_index_raises():
    with pytest.raises(ValueError, match="out of range"):
        _prepare([LTXMediaCondition(frames=_frames(1), latent_index=T_LAT)])


# -- IC-LoRA references ---------------------------------------------------------

def test_reference_coords_use_base_grid_with_causal_fix_origin_zero():
    p = _prepare([LTXMediaCondition(frames=_frames(9), role="reference", strength=1.0)], fill=4.0)
    n_ref = 2 * H_LAT * W_LAT  # 9 px frames -> 2 latent frames
    assert p.n_extra == n_ref
    starts = p.extra_coords[0, 0, :, 0]
    # Causal fix: (0*8 + 1 - 8).clamp(0) = 0; (1*8 + 1 - 8) = 1 -> origin 0.
    assert set(starts.tolist()) == {0.0, 1.0}
    assert torch.all(p.tokens[:, S_BASE:] == 4.0)
    assert torch.all(p.mask[:, S_BASE:] == 1.0)


def test_reference_single_frame_still_produces_one_latent_frame_of_tokens():
    # A still-image IC-LoRA reference is a single-pixel-frame
    # condition at this layer (source-agnostic -- the conditioning builder
    # never sees whether `frames` came from a video clip or a decoded still).
    # n_in=1 -> min(1, FRAMES)=1 -> (1-1)//8*8+1=1 pixel frame -> 1 latent frame.
    p = _prepare([LTXMediaCondition(frames=_frames(1), role="reference", strength=1.0)], fill=7.0)
    n_ref = 1 * H_LAT * W_LAT
    assert p.n_extra == n_ref
    assert torch.all(p.tokens[:, S_BASE:] == 7.0)
    assert torch.all(p.mask[:, S_BASE:] == 1.0)
    assert p.extra_coords.shape == (1, 3, n_ref, 2)


def test_reference_trimmed_to_generation_length():
    p = _prepare([LTXMediaCondition(frames=_frames(40), role="reference")])
    # min(40, 17) = 17 -> (17-1)//8*8+1 = 17 px frames -> 3 latent frames.
    assert p.n_extra == T_LAT * H_LAT * W_LAT


def test_keyframe_and_reference_append_in_order():
    p = _prepare([
        LTXMediaCondition(frames=_frames(1), latent_index=1, strength=0.5),
        LTXMediaCondition(frames=_frames(1), role="reference", strength=0.9),
    ])
    n_each = H_LAT * W_LAT
    assert p.n_extra == 2 * n_each
    assert torch.all(p.mask[:, S_BASE:S_BASE + n_each] == 0.5)
    assert torch.all(p.mask[:, S_BASE + n_each:] == 0.9)


# -- trim formula ----------------------------------------------------------------

@pytest.mark.parametrize("start,n_in,target,expected", [
    (0, 20, 17, 17),   # clipped to target then 1+8k
    (1, 20, 17, 9),    # 16 available -> 9
    (0, 5, 17, 1),     # short clip -> single frame
    (9, 17, 17, 1),    # tail keyframe
])
def test_trim_condition_frames(start, n_in, target, expected):
    assert _trim_condition_frames(start, n_in, target) == expected


# -- initial noise mix -------------------------------------------------------------

def test_mix_initial_noise_pins_and_noises_exactly():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=0, strength=1.0)], fill=5.0)
    noise = torch.randn(1, S_BASE, C)
    x = mix_initial_noise(p, noise, sigma0=1.0)
    n_tok = H_LAT * W_LAT
    assert torch.equal(x[:, :n_tok], p.tokens[:, :n_tok])     # mask=1 -> exactly clean
    assert torch.equal(x[:, n_tok:], noise[:, n_tok:])        # mask=0 -> exactly noise


def test_mix_initial_noise_partial_strength_blends():
    p = _prepare([LTXMediaCondition(frames=_frames(1), latent_index=0, strength=0.5)], fill=2.0)
    noise = torch.ones(1, S_BASE, C)
    x = mix_initial_noise(p, noise, sigma0=1.0)
    n_tok = H_LAT * W_LAT
    # scaled = (1-0.5)*1 = 0.5 -> x = 0.5*noise + 0.5*clean = 0.5*1 + 0.5*2 = 1.5
    assert torch.allclose(x[:, :n_tok], torch.full((1, n_tok, C), 1.5))


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="role"):
        _prepare([LTXMediaCondition(frames=_frames(1), role="banana")])


def _recording_encode(fill, seen):
    def enc(pixels):
        seen.append(tuple(pixels.shape))
        _, _, t, h, w = pixels.shape
        return torch.full((1, C, (t - 1) // 8 + 1, h // 32, w // 32), float(fill))

    return enc


def _prepare_at(conditions, encode, *, frames=FRAMES, height=128, width=128):
    return prepare_ltx_conditions(
        conditions, encode, frames=frames, height=height, width=width,
        device="cpu", dtype=torch.float32, latent_channels=C,
    )


def test_reference_target_tokens_start_from_noise_and_reference_tokens_start_clean():
    p = _prepare([LTXMediaCondition(frames=_frames(9), role="reference", strength=1.0)], fill=4.0)
    assert torch.all(p.tokens[:, :S_BASE] == 0.0) and torch.all(p.mask[:, :S_BASE] == 0.0)
    noise = torch.randn(1, S_BASE + p.n_extra, C)
    x = mix_initial_noise(p, noise, sigma0=1.0)
    assert torch.equal(x[:, :S_BASE], noise[:, :S_BASE])
    assert torch.all(x[:, S_BASE:] == 4.0)
    assert torch.equal(p.clean[:, S_BASE:], p.tokens[:, S_BASE:])


def test_reference_downscale_factor_encodes_a_smaller_reference_and_scales_coords_onto_target():
    seen = []
    p = _prepare_at([LTXMediaCondition(frames=_frames(9, 96, 96), role="reference", downscale_factor=2)],
                    _recording_encode(6.0, seen))
    assert seen == [(1, 3, 9, 64, 64)]
    assert p.base_tokens == T_LAT * 4 * 4
    assert p.n_extra == 2 * 2 * 2
    assert torch.all(p.tokens[:, p.base_tokens:] == 6.0)
    h_start, h_end = p.extra_coords[0, 1, :, 0], p.extra_coords[0, 1, :, 1]
    w_start, w_end = p.extra_coords[0, 2, :, 0], p.extra_coords[0, 2, :, 1]
    assert set(h_start.tolist()) == {0.0, 64.0} and set(h_end.tolist()) == {64.0, 128.0}
    assert set(w_start.tolist()) == {0.0, 64.0} and set(w_end.tolist()) == {64.0, 128.0}
    assert set(p.extra_coords[0, 0, :, 0].tolist()) == {0.0, 1.0}


def test_reference_without_downscale_keeps_target_size_and_unscaled_coords():
    seen = []
    p = _prepare_at([LTXMediaCondition(frames=_frames(9, 96, 96), role="reference")],
                    _recording_encode(6.0, seen))
    assert seen == [(1, 3, 9, 128, 128)]
    assert p.n_extra == 2 * 4 * 4
    assert p.extra_coords[0, 1, :, 1].max().item() == 128.0


def test_reference_temporal_scale_factor_subsamples_and_stretches_coords_over_the_target():
    seen = []
    p = prepare_ltx_conditions(
        [LTXMediaCondition(frames=_frames(17), role="reference", temporal_scale_factor=2)],
        _recording_encode(1.0, seen), frames=17, height=64, width=64,
        device="cpu", dtype=torch.float32, latent_channels=C)
    assert seen == [(1, 3, 9, 64, 64)]
    assert p.n_extra == 2 * H_LAT * W_LAT
    t_start, t_end = p.extra_coords[0, 0, :, 0], p.extra_coords[0, 0, :, 1]
    assert set(t_start.tolist()) == {0.0, 1.0}
    assert set(t_end.tolist()) == {1.0, 17.0}


def test_temporal_subsample_keeps_frame_zero_then_every_nth():
    frames = torch.arange(17, dtype=torch.float32).view(17, 1, 1, 1)
    kept = _temporal_subsample(frames, 2)
    assert kept.flatten().tolist() == [0.0, 1.0, 3.0, 5.0, 7.0, 9.0, 11.0, 13.0, 15.0]


def test_reference_downscale_factor_rejects_a_grid_it_does_not_divide():
    with pytest.raises(ValueError, match="multiples of 64"):
        _prepare_at([LTXMediaCondition(frames=_frames(9), role="reference", downscale_factor=2)],
                    _fake_encode(1.0), height=96, width=128)


def test_reference_scale_factors_rejected_on_keyframes():
    with pytest.raises(ValueError, match="role='reference'"):
        LTXMediaCondition(frames=_frames(1), latent_index=1, downscale_factor=2)
