"""Concat-layout tests for Wan i2v — verified against the ComfyUI construction.

Uses a fake vae_encode returning a known ref latent so the mask inversion, the
1->4 channel broadcast, the process_in normalization, and the (mask, ref) order
can be checked against hand-built expected tensors.
"""

from __future__ import annotations

import torch

from src.pipelines.pipes.generator.img2vid_wan22.concat import build_i2v_concat


def _fake_encode(fill):
    # returns (1, 16, T_lat, h, w) filled with `fill`, ignoring the pixel input's
    # content but honouring its spatial/temporal size.
    def enc(pixels):
        _, _, t, h, w = pixels.shape
        t_lat = (t - 1) // 4 + 1
        return torch.full((1, 16, t_lat, h // 8, w // 8), float(fill))
    return enc


def _concat(n_frames=1, length=5, mean=None, std=None, fill=3.0):
    start = torch.zeros(n_frames, 16, 16, 3)  # H=W=16 -> h_lat=w_lat=2
    return build_i2v_concat(
        start, _fake_encode(fill), length=length, height=16, width=16,
        latents_mean=mean or [0.0] * 16, latents_std=std or [1.0] * 16,
    )


def test_shape_is_20_channels_5d():
    c = _concat(length=5)  # t_lat = (5-1)//4 + 1 = 2
    assert c.shape == (1, 20, 2, 2, 2)  # 4 mask + 16 ref


def test_channel_order_mask_then_ref():
    c = _concat(fill=7.0, mean=[0.0] * 16, std=[1.0] * 16)
    mask, ref = c[:, :4], c[:, 4:]
    assert ref.shape[1] == 16
    assert torch.allclose(ref, torch.full_like(ref, 7.0))  # ref block = encoded latent


def test_single_start_frame_mask_is_1_at_frame0_only():
    c = _concat(n_frames=1, length=5)  # t_lat=2
    mask = c[:, :4]
    # inverted + broadcast: provided frame 0 -> 1 (all 4 channels), frame 1 -> 0.
    assert torch.all(mask[:, :, 0] == 1.0)
    assert torch.all(mask[:, :, 1] == 0.0)


def test_mask_broadcast_identical_across_4_channels():
    mask = _concat()[:, :4]
    for ch in range(1, 4):
        assert torch.equal(mask[:, 0], mask[:, ch])


def test_ref_is_process_in_normalized():
    # ref block == (encoded - mean) / std, per-channel.
    mean = [float(i) for i in range(16)]
    std = [2.0] * 16
    ref = _concat(fill=10.0, mean=mean, std=std)[:, 4:]
    expected_c0 = (10.0 - 0.0) / 2.0
    expected_c5 = (10.0 - 5.0) / 2.0
    assert torch.allclose(ref[:, 0], torch.full_like(ref[:, 0], expected_c0))
    assert torch.allclose(ref[:, 5], torch.full_like(ref[:, 5], expected_c5))


def test_more_start_frames_provide_more_masked_slots():
    # 5 start frames -> ((5-1)//4)+1 = 2 provided latent slots (of t_lat, longer video).
    c = _concat(n_frames=5, length=21)  # t_lat = (21-1)//4+1 = 6
    mask = c[:, :4]
    assert torch.all(mask[:, :, :2] == 1.0)   # first 2 latent frames provided
    assert torch.all(mask[:, :, 2:] == 0.0)   # rest generated


def _old_mask(n_provided: int, length: int) -> torch.Tensor:
    """The pre-FLF mask construction (latent-resolution zero+invert), kept here
    verbatim as the regression reference for the new pixel-packing construction."""
    t_lat = (length - 1) // 4 + 1
    mask = torch.ones((1, 1, t_lat, 1, 1))
    mask[:, :, : ((n_provided - 1) // 4) + 1] = 0.0
    mask = 1.0 - mask
    return mask.repeat(1, 4, 1, 1, 1)


def test_start_only_mask_matches_old_construction_exactly():
    # Regression: for start-only input (end_frames=None) the new pixel-packing
    # mask must be bit-identical to the old latent-resolution construction, for
    # a variety of start-frame counts and video lengths.
    for n_frames, length in [(1, 5), (1, 21), (5, 21), (9, 41), (1, 1)]:
        c = _concat(n_frames=n_frames, length=length)
        mask = c[:, :4]
        expected = _old_mask(n_frames, length).expand_as(mask)
        assert torch.equal(mask, expected), f"mismatch for n_frames={n_frames}, length={length}"


def test_motion_latent_count_pixel_frames_lock_exactly_n_slots():
    # SVI Pro tail hand-off: N latent slots == (N-1)*4+1 pixel frames. Feeding
    # that many start frames must lock EXACTLY N fully-provided latent slots in
    # the mask (verifies the chain pipe's tail-slice formula stays aligned with
    # the mask packing here).
    for n_slots in (1, 2, 3, 4):
        n_px = (n_slots - 1) * 4 + 1
        c = _concat(n_frames=n_px, length=41)  # t_lat = (41-1)//4+1 = 11
        mask = c[:, :4]
        assert torch.all(mask[:, :, :n_slots] == 1.0), f"n_slots={n_slots}: first {n_slots} not all locked"
        assert torch.all(mask[:, :, n_slots:] == 0.0), f"n_slots={n_slots}: slots past {n_slots} not free"


def test_anchor_strength_default_1_is_unchanged():
    # anchor_strength=1.0 must be byte-identical to omitting it (today's lock).
    baseline = _concat(n_frames=1, length=21, fill=5.0)
    start = torch.zeros(1, 16, 16, 3)
    scaled = build_i2v_concat(
        start, _fake_encode(5.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, anchor_strength=1.0,
    )
    assert torch.equal(scaled, baseline)


def test_anchor_strength_scales_only_the_start_mask():
    # A softened anchor scales the START (front) mask weight; the FLF end lock
    # stays hard at 1.0.
    start = torch.zeros(1, 16, 16, 3)
    end = torch.zeros(1, 16, 16, 3)
    c = build_i2v_concat(
        start, _fake_encode(3.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, end_frames=end,
        anchor_strength=0.7,
    )
    mask = c[:, :4]
    assert torch.allclose(mask[:, :, 0], torch.full_like(mask[:, :, 0], 0.7))  # anchor softened
    assert torch.all(mask[:, :, -1, :, :][:, 3] == 1.0)  # end frame still hard-locked
    assert torch.all(mask[:, :, 1:-1] == 0.0)            # mid frames still free


def test_anchor_frames_none_is_byte_identical():
    # The SVI-Pro anchor path is opt-in; anchor_frames=None must
    # reproduce today's construction exactly (plain i2v/flf regression guard).
    baseline = _concat(n_frames=1, length=21, fill=5.0)
    start = torch.zeros(1, 16, 16, 3)
    same = build_i2v_concat(
        start, _fake_encode(5.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, anchor_frames=None,
    )
    assert torch.equal(same, baseline)


def test_anchor_frames_lock_only_slot0_motion_tail_is_soft():
    # Anchor at slot 0 is mask-locked; the motion tail (start_frames) is placed
    # right after it as SOFT context -- its mask stays 0.
    anchor = torch.zeros(1, 16, 16, 3)
    motion = torch.zeros(5, 16, 16, 3)  # 5 px -> would be 2 latent slots if locked
    c = build_i2v_concat(
        motion, _fake_encode(3.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, anchor_frames=anchor,
    )
    mask = c[:, :4]
    # Only the anchor's latent slot 0 is locked; every later slot stays free.
    assert torch.all(mask[:, :, 0] == 1.0)
    assert torch.all(mask[:, :, 1:] == 0.0)


def test_anchor_frames_soften_lock_via_anchor_strength():
    anchor = torch.zeros(1, 16, 16, 3)
    motion = torch.zeros(1, 16, 16, 3)
    c = build_i2v_concat(
        motion, _fake_encode(3.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16,
        anchor_frames=anchor, anchor_strength=0.8,
    )
    mask = c[:, :4]
    assert torch.allclose(mask[:, :, 0], torch.full_like(mask[:, :, 0], 0.8))
    assert torch.all(mask[:, :, 1:] == 0.0)


def test_anchor_and_motion_tail_placed_in_buffer_order():
    # The VAE buffer must carry anchor at pixel 0 and the motion tail at pixels
    # 1..N (so the tail's content enters the reference latent as soft context).
    captured = {}

    def capturing_encode(pixels):
        captured["pixels"] = pixels.clone()
        _, _, t, h, w = pixels.shape
        t_lat = (t - 1) // 4 + 1
        return torch.zeros((1, 16, t_lat, h // 8, w // 8))

    anchor = torch.ones(1, 16, 16, 3)          # pixel 1.0 -> +1.0 after [-1,1] map
    motion = torch.full((2, 16, 16, 3), 0.75)  # distinguishable from grey/anchor

    build_i2v_concat(
        motion, capturing_encode, length=9, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, anchor_frames=anchor,
    )

    pixels = captured["pixels"]  # (1, 3, length, H, W) in [-1, 1]
    assert torch.allclose(pixels[:, :, 0], torch.ones_like(pixels[:, :, 0]))          # anchor at 0
    assert torch.allclose(pixels[:, :, 1], torch.full_like(pixels[:, :, 1], 0.5))     # motion (0.75->0.5)
    assert torch.allclose(pixels[:, :, 2], torch.full_like(pixels[:, :, 2], 0.5))
    assert torch.allclose(pixels[:, :, 3], torch.zeros_like(pixels[:, :, 3]))         # grey (0.5->0.0)


def _concat_flf(n_start=1, n_end=1, length=21, fill=3.0):
    start = torch.zeros(n_start, 16, 16, 3)
    end = torch.zeros(n_end, 16, 16, 3)
    return build_i2v_concat(
        start, _fake_encode(fill), length=length, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, end_frames=end,
    )


def test_flf_last_latent_frame_mask_is_0001():
    # length=21 -> t_lat=6; last latent frame's 4 mask channels should be
    # [0, 0, 0, 1] (only its final pixel slot is the real end frame).
    c = _concat_flf(n_start=1, n_end=1, length=21)
    mask = c[:, :4]
    last = mask[:, :, -1]  # (1, 4, h, w)
    assert torch.all(last[:, 0] == 0.0)
    assert torch.all(last[:, 1] == 0.0)
    assert torch.all(last[:, 2] == 0.0)
    assert torch.all(last[:, 3] == 1.0)


def test_flf_first_latent_frame_mask_is_1111():
    c = _concat_flf(n_start=1, n_end=1, length=21)
    mask = c[:, :4]
    first = mask[:, :, 0]
    assert torch.all(first == 1.0)


def test_flf_middle_latent_frames_are_zero():
    c = _concat_flf(n_start=1, n_end=1, length=21)  # t_lat=6
    mask = c[:, :4]
    middle = mask[:, :, 1:-1]  # latent frames 1..4
    assert torch.all(middle == 0.0)


def test_end_frames_placed_at_buffer_tail():
    # Probe via a fake vae_encode that captures the pixel buffer it was called
    # with, so we can check the end frame(s) landed at the tail (not the front
    # or middle) of the (length, H, W, 3) grey buffer.
    captured = {}

    def capturing_encode(pixels):
        captured["pixels"] = pixels.clone()
        _, _, t, h, w = pixels.shape
        t_lat = (t - 1) // 4 + 1
        return torch.zeros((1, 16, t_lat, h // 8, w // 8))

    start = torch.zeros(1, 16, 16, 3)  # grey buffer value 0.5 -> pixel value 0.0
    end = torch.ones(1, 16, 16, 3)     # pixel value 1.0 -> distinguishable

    build_i2v_concat(
        start, capturing_encode, length=5, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, end_frames=end,
    )

    # captured pixels: (1, 3, length, H, W) in [-1, 1]; end frame (pixel=1.0) -> 1.0.
    pixels = captured["pixels"]
    assert torch.allclose(pixels[:, :, -1], torch.ones_like(pixels[:, :, -1]))
    # start frame (pixel=0.0) -> -1.0
    assert torch.allclose(pixels[:, :, 0], -torch.ones_like(pixels[:, :, 0]))
    # mid frames stay grey (0.5 -> 0.0)
    assert torch.allclose(pixels[:, :, 1:-1], torch.zeros_like(pixels[:, :, 1:-1]))


def test_end_frames_none_behaves_identically_to_before():
    # end_frames=None must reproduce the exact old (pre-FLF) output, including
    # the ref latent block (unaffected by the mask change, but checked for
    # completeness) and the mask.
    c_new = _concat(n_frames=1, length=21, fill=5.0)
    c_old_mask = _old_mask(1, 21).expand(1, 4, 6, 2, 2)
    assert torch.equal(c_new[:, :4], c_old_mask)
    assert torch.allclose(c_new[:, 4:], torch.full_like(c_new[:, 4:], 5.0))


# -- tail_latent splice (seam hand-off, bypasses decode/re-encode) --

def test_tail_latent_none_is_byte_identical():
    # Omitting tail_latent (default) must reproduce today's construction exactly.
    baseline = _concat(n_frames=1, length=21, fill=5.0)
    start = torch.zeros(1, 16, 16, 3)
    same = build_i2v_concat(
        start, _fake_encode(5.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, tail_latent=None,
    )
    assert torch.equal(same, baseline)


def test_tail_latent_overwrites_leading_ref_slots_only():
    # tail_latent replaces ref's first n slots verbatim; later slots keep
    # whatever the normal encode+normalize path produced.
    start = torch.zeros(1, 16, 16, 3)
    mean, std = [0.0] * 16, [1.0] * 16
    tail = torch.full((1, 16, 2, 2, 2), 99.0)  # 2 latent slots, distinguishable
    c = build_i2v_concat(
        start, _fake_encode(3.0), length=21, height=16, width=16,  # t_lat=6
        latents_mean=mean, latents_std=std, tail_latent=tail,
    )
    ref = c[:, 4:]
    assert torch.allclose(ref[:, :, :2], torch.full_like(ref[:, :, :2], 99.0))
    assert torch.allclose(ref[:, :, 2:], torch.full_like(ref[:, :, 2:], 3.0))  # untouched


def test_tail_latent_mask_unaffected_by_splice():
    # The mask is driven entirely by start_frames/anchor_frames counts, same as
    # without tail_latent -- splicing the ref doesn't touch it.
    start = torch.zeros(1, 16, 16, 3)  # 1 start frame -> mask locks slot 0 only
    tail = torch.full((1, 16, 1, 2, 2), 42.0)
    with_tail = build_i2v_concat(
        start, _fake_encode(3.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16, tail_latent=tail,
    )
    without_tail = build_i2v_concat(
        start, _fake_encode(3.0), length=21, height=16, width=16,
        latents_mean=[0.0] * 16, latents_std=[1.0] * 16,
    )
    assert torch.equal(with_tail[:, :4], without_tail[:, :4])


def test_tail_latent_matches_pixel_roundtrip_under_identity_vae():
    # Space-handling proof: a raw sampled latent (the `latent` param, already in
    # process_latent_in-normalized "model space") spliced directly via
    # tail_latent must land at the SAME ref value as the plain pixel path
    # would produce for the "same" content, when the fake vae_encode always
    # returns the RAW (pre-normalize) latent that a real decode->encode round
    # trip of that content would reproduce (i.e. an identity encode/decode
    # pair). This is exactly the assertion the pipe relies on to splice
    # WITHOUT re-normalizing: if the sampler's space and ref's post-normalize
    # space disagreed, this equality would fail.
    mean = [float(i) for i in range(16)]  # non-trivial, so a missed/extra
    std = [2.0] * 16                      # normalize step would show up.
    raw_vae_value = 10.0  # what vae_encode(pixels) would produce (pre-normalize)

    start = torch.zeros(1, 16, 16, 3)
    pixel_path = build_i2v_concat(
        start, _fake_encode(raw_vae_value), length=5, height=16, width=16,
        latents_mean=mean, latents_std=std,
    )
    ref_pixel = pixel_path[:, 4:]

    # The sampler's own latent for that same content: process_latent_in
    # applied once, exactly as _decode_video's inverse (z*std+mean) implies.
    mean_t = torch.tensor(mean).view(1, -1, 1, 1, 1)
    std_t = torch.tensor(std).view(1, -1, 1, 1, 1)
    sampled_latent = (torch.full((1, 16, 2, 2, 2), raw_vae_value) - mean_t) / std_t

    latent_path = build_i2v_concat(
        start, _fake_encode(raw_vae_value), length=5, height=16, width=16,
        latents_mean=mean, latents_std=std, tail_latent=sampled_latent,
    )
    ref_latent = latent_path[:, 4:]

    assert torch.allclose(ref_latent, ref_pixel)
