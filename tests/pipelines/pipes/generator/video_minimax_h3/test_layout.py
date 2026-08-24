"""Tests for the MiniMax-H3 packed-sequence layout builder.

Per the port brief: the reference math (`before_denoise.py`'s float64 grids,
`np.linspace(endpoint=False)` semantics, the non-uniform temporal spacing, and
the PAIRWISE-summed "last" keyframe anchor) is re-implemented INDEPENDENTLY
here, directly in the test, rather than re-importing `layout.py`'s own
helpers for the "reference" side of each comparison -- so a bug shared by
both sides can't hide. CPU-only, tiny configs, no weights.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.layout import (
    AUDIO_TAG,
    TEXT_TAG,
    VIDEO_TAG,
    ReferenceBlock,
    build_packed_sequence,
    build_ref2va_packed_sequence,
    build_row_timesteps,
    patchify_video_latents,
    unpatchify_video_rows,
)

PATCH = (1, 2, 2)


# -- independent reference implementation (do NOT import layout.py's own) ----

_FRAME_RESCALE = 5.0 / 3.0
_FRAMES_PER_LATENT = (1, 4, 4, 4, 4)
_SPATIAL_SCALE = 32


def _ref_spatial_grid(dim: int, patch: int, sqrt_area: float) -> np.ndarray:
    ratio = dim / sqrt_area
    left = (1.0 - ratio) / 2.0
    # Deliberately np.linspace(endpoint=False), independently re-typed from
    # the dossier's own formula rather than copy-pasted from layout.py:
    # start + arange(n) * (stop-start)/n.
    n = dim // patch
    return (left + np.arange(n) * ratio / n) * _SPATIAL_SCALE


def _ref_frame_grid(latent_h: int, latent_w: int, patch_h: int, patch_w: int) -> tuple[np.ndarray, np.ndarray]:
    sqrt_area = np.sqrt(latent_h * latent_w)
    h_grid = _ref_spatial_grid(latent_h, patch_h, sqrt_area)
    w_grid = _ref_spatial_grid(latent_w, patch_w, sqrt_area)
    hh, ww = np.meshgrid(h_grid, w_grid, indexing="ij")
    return np.stack([hh.reshape(-1), ww.reshape(-1)], axis=-1), w_grid


def _ref_temporal_grid(num_latent_frames: int, origin: float) -> np.ndarray:
    spans = np.array(
        [_FRAME_RESCALE * _FRAMES_PER_LATENT[i % len(_FRAMES_PER_LATENT)] for i in range(num_latent_frames)],
        dtype=np.float64,
    )
    out = np.empty(num_latent_frames, dtype=np.float64)
    out[0] = origin
    out[1:] = origin + np.cumsum(spans[:-1])
    return out


def _ref_pairwise_span_sum(num_latent_frames: int) -> float:
    spans = np.ones(num_latent_frames, dtype=np.float64) * _FRAME_RESCALE
    for offset in range(len(_FRAMES_PER_LATENT)):
        spans[offset :: len(_FRAMES_PER_LATENT)] *= _FRAMES_PER_LATENT[offset]
    return float(spans.sum())


def _ref_build_packed_sequence(
    num_text_tokens: int, *, num_latent_frames: int, latent_h: int, latent_w: int,
    num_audio_latents: int, keyframe_anchors: tuple[str, ...] = (), audio_channels: int = 2,
) -> dict:
    patch_h, patch_w = PATCH[1], PATCH[2]
    rows_per_frame = (latent_h // patch_h) * (latent_w // patch_w)
    num_condition_rows = len(keyframe_anchors) * rows_per_frame
    num_audio_rows = num_audio_latents * audio_channels
    num_video_rows = num_latent_frames * rows_per_frame
    seq_len = num_text_tokens + num_condition_rows + num_audio_rows + num_video_rows

    condition_start = num_text_tokens
    audio_start = condition_start + num_condition_rows
    video_start = audio_start + num_audio_rows

    position_ids = np.zeros((seq_len, 3), dtype=np.float64)
    position_ids[:num_text_tokens, 0] = np.arange(num_text_tokens, dtype=np.float64)

    frame_grid, w_grid = _ref_frame_grid(latent_h, latent_w, patch_h, patch_w)

    for index, anchor in enumerate(keyframe_anchors):
        if anchor == "first":
            anchor_time = float(num_text_tokens)
        else:
            anchor_time = float(num_text_tokens) + _ref_pairwise_span_sum(num_latent_frames) - _FRAME_RESCALE
        rows = slice(condition_start + index * rows_per_frame, condition_start + (index + 1) * rows_per_frame)
        position_ids[rows, 0] = anchor_time
        position_ids[rows, 1:] = frame_grid

    audio_time = float(num_text_tokens) + np.arange(num_audio_latents, dtype=np.float64)
    position_ids[audio_start:video_start, 0] = np.tile(audio_time, audio_channels)
    position_ids[audio_start:video_start, 2] = np.concatenate([
        np.full(num_audio_latents, w_grid[0]), np.full(num_audio_rows - num_audio_latents, w_grid[-1]),
    ])

    video_pos = np.empty((num_latent_frames, rows_per_frame, 3), dtype=np.float64)
    video_pos[:, :, 0] = _ref_temporal_grid(num_latent_frames, float(num_text_tokens))[:, None]
    video_pos[:, :, 1:] = frame_grid[None]
    position_ids[video_start:] = video_pos.reshape(-1, 3)

    video_indices = np.concatenate([np.arange(condition_start, audio_start), np.arange(video_start, seq_len)])
    audio_indices = np.arange(audio_start, video_start)
    text_indices = np.arange(num_text_tokens)

    return dict(
        position_ids=position_ids, video_indices=video_indices, audio_indices=audio_indices,
        text_indices=text_indices, num_condition_video_rows=num_condition_rows,
    )


# -- t2va: no conditioning ----------------------------------------------------

def test_t2va_layout_matches_independent_reference():
    num_text_tokens = 5
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=3, latent_height=4, latent_width=4,
        num_audio_latents=6, patch_size=PATCH,
    )
    ref = _ref_build_packed_sequence(num_text_tokens, num_latent_frames=3, latent_h=4, latent_w=4, num_audio_latents=6)

    assert layout.num_condition_video_rows == 0
    np.testing.assert_allclose(layout.position_ids.numpy(), ref["position_ids"], rtol=0, atol=0)
    np.testing.assert_array_equal(layout.video_indices.numpy(), ref["video_indices"])
    np.testing.assert_array_equal(layout.audio_indices.numpy(), ref["audio_indices"])
    np.testing.assert_array_equal(layout.text_indices.numpy(), ref["text_indices"])
    assert layout.position_ids.dtype == torch.float64


def test_t2va_token_tags_are_video_for_video_rows_audio_for_audio_rows():
    num_text_tokens = 4
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3, patch_size=PATCH,
    )
    assert torch.all(layout.token_tags[layout.text_indices] == TEXT_TAG)
    assert torch.all(layout.token_tags[layout.video_indices] == VIDEO_TAG)
    assert torch.all(layout.token_tags[layout.audio_indices] == AUDIO_TAG)


def test_a_keyframes_vision_block_text_rows_stay_video_tagged():
    # dossier trap 6: a keyframe's own vision-block TEXT rows are tagged
    # VIDEO(0), not TEXT(1) -- this module only consumes text_token_tags, so
    # feeding it a tag array with a VIDEO-tagged prefix must survive
    # untouched into the packed token_tags at those same text positions.
    text_tags = torch.tensor([VIDEO_TAG, VIDEO_TAG, TEXT_TAG, TEXT_TAG, TEXT_TAG], dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
        keyframe_anchors=("first",),
    )
    assert torch.equal(layout.token_tags[layout.text_indices], text_tags)


# -- fl2va: keyframe anchors, incl. the pairwise-sum "last" case -------------

@pytest.mark.parametrize("num_latent_frames", [2, 8, 17, 32])
def test_fl2va_first_and_last_anchor_positions_match_independent_reference(num_latent_frames):
    num_text_tokens = 7
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=num_latent_frames, latent_height=4, latent_width=4,
        num_audio_latents=5, patch_size=PATCH, keyframe_anchors=("first", "last"),
    )
    ref = _ref_build_packed_sequence(
        num_text_tokens, num_latent_frames=num_latent_frames, latent_h=4, latent_w=4,
        num_audio_latents=5, keyframe_anchors=("first", "last"),
    )
    np.testing.assert_allclose(layout.position_ids.numpy(), ref["position_ids"], rtol=0, atol=0)
    assert layout.num_condition_video_rows == ref["num_condition_video_rows"]


def test_last_anchor_pairwise_vs_sequential_sum_differ_from_16_latent_frames():
    # Establishes the premise the next test's BITE CHECK relies on: at 16+
    # latent frames the numpy-pairwise sum and a plain sequential Python sum
    # of the SAME series genuinely diverge in the last ulp (dossier trap 4).
    # Below 16 frames they must agree (nothing to distinguish the two sums).
    def sequential_sum(n: int) -> float:
        total = 0.0
        for i in range(n):
            total += _FRAME_RESCALE * _FRAMES_PER_LATENT[i % len(_FRAMES_PER_LATENT)]
        return total

    for n in (2, 8, 15):
        assert _ref_pairwise_span_sum(n) == sequential_sum(n)
    diverges = [n for n in range(16, 40) if _ref_pairwise_span_sum(n) != sequential_sum(n)]
    assert diverges, "expected at least one n in [16,40) where pairwise and sequential sums diverge"


def test_bite_check_last_anchor_uses_pairwise_not_sequential_sum():
    # BITE CHECK: swap the builder's own pairwise sum for a sequential one at
    # a divergent n and confirm the layout NO LONGER matches -- proves the
    # comparison above is actually sensitive to the summation order, not
    # vacuously equal.
    def sequential_sum(n: int) -> float:
        total = 0.0
        for i in range(n):
            total += _FRAME_RESCALE * _FRAMES_PER_LATENT[i % len(_FRAMES_PER_LATENT)]
        return total

    n = next(k for k in range(16, 40) if _ref_pairwise_span_sum(k) != sequential_sum(k))
    num_text_tokens = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=n, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
        keyframe_anchors=("last",),
    )
    correct_anchor_time = float(num_text_tokens) + _ref_pairwise_span_sum(n) - _FRAME_RESCALE
    wrong_anchor_time = float(num_text_tokens) + sequential_sum(n) - _FRAME_RESCALE
    assert wrong_anchor_time != correct_anchor_time
    got_anchor_time = float(layout.position_ids[num_text_tokens, 0])
    assert got_anchor_time == correct_anchor_time
    assert got_anchor_time != wrong_anchor_time


def test_invalid_anchor_rejected():
    text_tags = torch.zeros(1, dtype=torch.long)
    with pytest.raises(ValueError):
        build_packed_sequence(
            text_tags, num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1,
            patch_size=PATCH, keyframe_anchors=("middle",),
        )


# -- "first"/"last" anchor values are FROZEN ---------------------------------

# Captured from the builder BEFORE integer anchors and condition-audio rows
# existed, as exact float64 hex so a last-ulp drift cannot pass. `"last"`
# rides the numpy-pairwise sum; anything that quietly re-routes it through a
# sequential cumsum (e.g. "simplifying" it to the temporal grid's last entry)
# changes these and silently changes existing fl2va output.
_FROZEN_ANCHORS = {
    3: ("0x1.c000000000000p+2", "0x1.4555555555555p+4"),
    8: ("0x1.c000000000000p+2", "0x1.8555555555556p+5"),
    17: ("0x1.c000000000000p+2", "0x1.8aaaaaaaaaaabp+6"),
    37: ("0x1.c000000000000p+2", "0x1.a7fffffffffffp+7"),
}


@pytest.mark.parametrize("num_latent_frames", sorted(_FROZEN_ANCHORS))
def test_first_and_last_anchor_times_are_bit_frozen(num_latent_frames):
    num_text_tokens = 7
    rows_per_frame = 4  # latent 4x4 over patch (1,2,2)
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=num_latent_frames, latent_height=4, latent_width=4,
        num_audio_latents=5, patch_size=PATCH, keyframe_anchors=("first", "last"),
    )
    first = float(layout.position_ids[num_text_tokens, 0])
    last = float(layout.position_ids[num_text_tokens + rows_per_frame, 0])
    assert (first.hex(), last.hex()) == _FROZEN_ANCHORS[num_latent_frames]


# -- integer latent-frame anchors ---------------------------------------------

@pytest.mark.parametrize("num_latent_frames", [1, 5, 17])
def test_integer_anchor_equals_that_latent_frames_own_rotary_time(num_latent_frames):
    num_text_tokens = 6
    rows_per_frame = 4
    anchors = tuple(range(num_latent_frames))
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=num_latent_frames, latent_height=4, latent_width=4,
        num_audio_latents=5, patch_size=PATCH, keyframe_anchors=anchors,
    )
    expected = _ref_temporal_grid(num_latent_frames, float(num_text_tokens))
    for k in anchors:
        got = float(layout.position_ids[num_text_tokens + k * rows_per_frame, 0])
        assert got == expected[k], f"anchor {k}"


def test_integer_anchor_zero_is_bit_identical_to_first():
    num_text_tokens = 7
    rows_per_frame = 4
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=17, latent_height=4, latent_width=4,
        num_audio_latents=5, patch_size=PATCH, keyframe_anchors=("first", 0),
    )
    by_name = float(layout.position_ids[num_text_tokens, 0])
    by_index = float(layout.position_ids[num_text_tokens + rows_per_frame, 0])
    assert by_name.hex() == by_index.hex()


def test_integer_anchor_last_frame_is_not_the_last_anchor_past_16_frames():
    # The documented divergence, made explicit so nobody "fixes" it: the
    # `"last"` anchor is the pairwise span sum, NOT the temporal grid's last
    # entry, and from 16 latent frames on the two are different float64s.
    num_text_tokens = 3
    rows_per_frame = 4
    n = next(
        k for k in range(16, 60)
        if _ref_pairwise_span_sum(k) - _FRAME_RESCALE != _ref_temporal_grid(k, 0.0)[-1]
    )
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=n, latent_height=4, latent_width=4,
        num_audio_latents=5, patch_size=PATCH, keyframe_anchors=("last", n - 1),
    )
    by_name = float(layout.position_ids[num_text_tokens, 0])
    by_index = float(layout.position_ids[num_text_tokens + rows_per_frame, 0])
    assert by_index == _ref_temporal_grid(n, float(num_text_tokens))[-1]
    assert by_name != by_index


@pytest.mark.parametrize("anchor", [-1, 3, 99])
def test_out_of_range_integer_anchor_rejected(anchor):
    text_tags = torch.zeros(2, dtype=torch.long)
    with pytest.raises(ValueError):
        build_packed_sequence(
            text_tags, num_latent_frames=3, latent_height=2, latent_width=2, num_audio_latents=1,
            patch_size=PATCH, keyframe_anchors=(anchor,),
        )


def test_bool_anchor_rejected_rather_than_read_as_frame_index():
    text_tags = torch.zeros(2, dtype=torch.long)
    with pytest.raises(ValueError):
        build_packed_sequence(
            text_tags, num_latent_frames=3, latent_height=2, latent_width=2, num_audio_latents=1,
            patch_size=PATCH, keyframe_anchors=(True,),
        )


def test_anchors_are_laid_out_in_the_order_given():
    num_text_tokens = 4
    rows_per_frame = 4
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    forward = build_packed_sequence(
        text_tags, num_latent_frames=6, latent_height=4, latent_width=4, num_audio_latents=3,
        patch_size=PATCH, keyframe_anchors=(0, 5),
    )
    reversed_ = build_packed_sequence(
        text_tags, num_latent_frames=6, latent_height=4, latent_width=4, num_audio_latents=3,
        patch_size=PATCH, keyframe_anchors=(5, 0),
    )
    grid = _ref_temporal_grid(6, float(num_text_tokens))
    assert float(forward.position_ids[num_text_tokens, 0]) == grid[0]
    assert float(forward.position_ids[num_text_tokens + rows_per_frame, 0]) == grid[5]
    assert float(reversed_.position_ids[num_text_tokens, 0]) == grid[5]
    assert float(reversed_.position_ids[num_text_tokens + rows_per_frame, 0]) == grid[0]


# -- audio stereo channel-major placement ------------------------------------

def test_audio_rows_have_no_height_coordinate_and_pin_width_extremes():
    num_text_tokens = 2
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=1, latent_height=4, latent_width=4, num_audio_latents=3, patch_size=PATCH,
    )
    audio_pos = layout.position_ids[layout.audio_indices]
    assert torch.all(audio_pos[:, 1] == 0.0)
    left_block, right_block = audio_pos[:3], audio_pos[3:]
    assert torch.all(left_block[:, 2] == left_block[0, 2])
    assert torch.all(right_block[:, 2] == right_block[0, 2])
    assert left_block[0, 2] != right_block[0, 2]
    # 1 rotary unit per audio latent, same clock origin as video/text.
    np.testing.assert_allclose(
        left_block[:, 0].numpy(), num_text_tokens + np.arange(3, dtype=np.float64),
    )


# -- condition audio rows ------------------------------------------------------

def test_zero_condition_audio_latents_leaves_the_layout_untouched():
    # The default path must be byte-identical to the t2va/fl2va reference the
    # module already matched -- an explicit 0 changes nothing at all.
    num_text_tokens = 5
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    kwargs = dict(
        num_latent_frames=3, latent_height=4, latent_width=4, num_audio_latents=6,
        patch_size=PATCH, keyframe_anchors=("first", "last"),
    )
    default = build_packed_sequence(text_tags, **kwargs)
    explicit = build_packed_sequence(text_tags, num_condition_audio_latents=0, **kwargs)
    ref = _ref_build_packed_sequence(
        num_text_tokens, num_latent_frames=3, latent_h=4, latent_w=4, num_audio_latents=6,
        keyframe_anchors=("first", "last"),
    )
    for layout in (default, explicit):
        assert layout.num_condition_audio_rows == 0
        assert layout.media_rotary_origin == float(num_text_tokens)
        np.testing.assert_allclose(layout.position_ids.numpy(), ref["position_ids"], rtol=0, atol=0)
        np.testing.assert_array_equal(layout.audio_indices.numpy(), ref["audio_indices"])
        np.testing.assert_array_equal(layout.video_indices.numpy(), ref["video_indices"])


def test_condition_audio_rows_counted_and_tagged():
    num_text_tokens = 4
    num_condition = 3
    num_audio_latents = 5
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=4, latent_width=4,
        num_audio_latents=num_audio_latents, patch_size=PATCH, keyframe_anchors=("first",),
        num_condition_audio_latents=num_condition,
    )
    assert layout.num_condition_audio_rows == num_condition * 2
    assert layout.audio_indices.numel() == (num_condition + num_audio_latents) * 2
    assert torch.all(layout.token_tags[layout.audio_indices] == AUDIO_TAG)
    # Every row belongs to exactly one stream, and the three streams tile the
    # whole sequence -- the invariant build_row_timesteps' length arithmetic
    # depends on.
    every = torch.cat([layout.text_indices, layout.audio_indices, layout.video_indices]).sort().values
    assert torch.equal(every, torch.arange(layout.position_ids.shape[0]))


def test_condition_audio_rows_are_a_contiguous_prefix_of_the_audio_stream():
    num_text_tokens = 4
    num_condition = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=5,
        patch_size=PATCH, keyframe_anchors=("first",), num_condition_audio_latents=num_condition,
    )
    condition = layout.audio_indices[: layout.num_condition_audio_rows]
    target = layout.audio_indices[layout.num_condition_audio_rows :]
    assert torch.equal(condition, torch.arange(num_text_tokens, num_text_tokens + num_condition * 2))
    assert torch.equal(target, torch.arange(int(target[0]), int(target[-1]) + 1))
    assert int(condition[-1]) < int(target[0])


def test_condition_video_rows_stay_a_contiguous_prefix_with_condition_audio_present():
    num_text_tokens = 4
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=5,
        patch_size=PATCH, keyframe_anchors=("first", "last"), num_condition_audio_latents=3,
    )
    condition = layout.video_indices[: layout.num_condition_video_rows]
    assert torch.equal(condition, torch.arange(int(condition[0]), int(condition[-1]) + 1))
    assert torch.all(layout.token_tags[condition] == VIDEO_TAG)
    # ...and they sit after the condition-audio block, not before it.
    assert int(condition[0]) == num_text_tokens + layout.num_condition_audio_rows


def test_condition_audio_occupies_the_clock_before_the_target():
    # The ref2va rule: an audio block fills at the running rotary time and
    # advances it by one unit per latent, so the generated rows start after it.
    num_text_tokens = 2
    num_condition = 4
    num_audio_latents = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=4, latent_width=4,
        num_audio_latents=num_audio_latents, patch_size=PATCH,
        num_condition_audio_latents=num_condition,
    )
    assert layout.media_rotary_origin == float(num_text_tokens + num_condition)

    audio_pos = layout.position_ids[layout.audio_indices]
    condition_left = audio_pos[:num_condition, 0].numpy()
    target_left = audio_pos[layout.num_condition_audio_rows :][:num_audio_latents, 0].numpy()
    np.testing.assert_allclose(condition_left, num_text_tokens + np.arange(num_condition, dtype=np.float64))
    np.testing.assert_allclose(
        target_left, layout.media_rotary_origin + np.arange(num_audio_latents, dtype=np.float64)
    )
    # The condition block's last latent abuts the target's first.
    assert condition_left[-1] + 1.0 == target_left[0]


def test_condition_audio_shifts_target_video_and_keyframe_anchors_together():
    num_text_tokens = 2
    num_condition = 4
    num_latent_frames = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=num_latent_frames, latent_height=4, latent_width=4,
        num_audio_latents=3, patch_size=PATCH, keyframe_anchors=("first",),
        num_condition_audio_latents=num_condition,
    )
    origin = float(num_text_tokens + num_condition)
    expected_frames = _ref_temporal_grid(num_latent_frames, origin)

    target_video = layout.video_indices[layout.num_condition_video_rows :]
    got_frames = layout.position_ids[target_video, 0].numpy().reshape(num_latent_frames, -1)
    np.testing.assert_allclose(got_frames, np.repeat(expected_frames[:, None], got_frames.shape[1], axis=1))
    # The "first" keyframe anchor overlays target frame 0, so it moved too.
    assert float(layout.position_ids[layout.video_indices[0], 0]) == expected_frames[0]


def test_condition_audio_rows_pin_the_same_stereo_width_extremes_as_the_target():
    text_tags = torch.full((2,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=1, latent_height=4, latent_width=8, num_audio_latents=3,
        patch_size=PATCH, num_condition_audio_latents=2,
    )
    audio_pos = layout.position_ids[layout.audio_indices]
    condition, target = audio_pos[:4], audio_pos[4:]
    assert torch.all(audio_pos[:, 1] == 0.0)
    # L block -> width_grid[0], R block -> width_grid[-1], for BOTH blocks;
    # a standalone audio block uses the TARGET width grid in the reference.
    assert torch.all(condition[:2, 2] == target[0, 2])
    assert torch.all(condition[2:, 2] == target[-1, 2])
    assert target[0, 2] != target[-1, 2]


def test_negative_condition_audio_latents_rejected():
    text_tags = torch.zeros(2, dtype=torch.long)
    with pytest.raises(ValueError):
        build_packed_sequence(
            text_tags, num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1,
            patch_size=PATCH, num_condition_audio_latents=-1,
        )


def test_build_row_timesteps_pins_condition_audio_to_its_own_timestep():
    # build_row_timesteps needs no change for condition audio: it slices the
    # prefix off the SAME audio_indices the layout hands it.
    num_text_tokens = 2
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_packed_sequence(
        text_tags, num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=3,
        patch_size=PATCH, keyframe_anchors=("first",), num_condition_audio_latents=2,
    )
    unique_ts, ts_idx = build_row_timesteps(
        layout.video_indices, layout.audio_indices,
        num_condition_video_rows=layout.num_condition_video_rows,
        num_condition_audio_rows=layout.num_condition_audio_rows,
        num_text_tokens=num_text_tokens, video_timestep=0.4, audio_timestep=0.7,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    assert ts_idx.shape == (layout.position_ids.shape[0],)
    row_values = unique_ts[ts_idx]
    n_ca, n_cv = layout.num_condition_audio_rows, layout.num_condition_video_rows
    assert torch.all(row_values[layout.audio_indices[:n_ca]] == 1.0)
    assert torch.all(row_values[layout.audio_indices[n_ca:]] == 0.7)
    assert torch.all(row_values[layout.video_indices[:n_cv]] == 0.999)
    assert torch.all(row_values[layout.video_indices[n_cv:]] == 0.4)
    assert torch.all(row_values[layout.text_indices] == 0.4)


# -- patchify / unpatchify roundtrip ------------------------------------------

def test_patchify_unpatchify_roundtrip():
    latents = torch.randn(1, 24, 3, 4, 4, dtype=torch.float64)
    rows = patchify_video_latents(latents, PATCH)
    assert rows.shape == (3 * 2 * 2, 24 * 1 * 2 * 2)
    back = unpatchify_video_rows(
        rows, num_latent_frames=3, latent_height=4, latent_width=4, channels=24, patch_size=PATCH,
    )
    assert torch.equal(back, latents)


def test_patchify_rejects_indivisible_shape():
    latents = torch.randn(1, 24, 3, 3, 4)  # height 3 not divisible by patch_h=2
    with pytest.raises(ValueError):
        patchify_video_latents(latents, PATCH)


# -- row timestep plan ---------------------------------------------------------

def test_build_row_timesteps_condition_rows_pinned_generated_rows_scheduled():
    # A real [text | condition_video | audio | video_target] layout with
    # num_text_tokens=3: text {0,1,2}, condition video {3,4}, audio {5,6,7},
    # target video {8,9,10,11} -- indices disjoint and cover range(12) once
    # each, same invariant build_packed_sequence's own output satisfies.
    video_indices = torch.tensor([3, 4, 8, 9, 10, 11])
    audio_indices = torch.tensor([5, 6, 7])
    unique_ts, ts_idx = build_row_timesteps(
        video_indices, audio_indices, num_condition_video_rows=2, num_condition_audio_rows=0,
        num_text_tokens=3, video_timestep=0.4, audio_timestep=0.7,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    seq_len = 3 + 6 + 3  # text + video(incl. condition) + audio
    assert ts_idx.shape == (seq_len,)
    row_values = unique_ts[ts_idx]
    assert torch.allclose(row_values[[0, 1, 2]], torch.full((3,), 0.4))       # text inherits video's timestep
    assert torch.allclose(row_values[[3, 4]], torch.full((2,), 0.999))        # condition video rows, pinned
    assert torch.allclose(row_values[[5, 6, 7]], torch.full((3,), 0.7))       # audio rows
    assert torch.allclose(row_values[[8, 9, 10, 11]], torch.full((4,), 0.4))  # generated video rows


# -- device coercion (the "cuda:0 vs cpu" real-GPU crash) --------------------
#
# prompt_encoder's conditioning (context + text_token_tags) is produced on
# CPU while the generator's sampler state lives on the generation device.
# `text_token_tags`/`video_indices`/`audio_indices` cross that boundary into
# these two functions, which used to build their OWN internal tensors via
# bare `torch.zeros`/`torch.arange`/`torch.full` (implicit CPU) regardless of
# what device the caller-supplied tensor actually carried -- an indexed
# assignment between the two then raised `RuntimeError: ... cuda:0 vs cpu`
# on a real GPU run. Both functions now build every tensor they own directly
# on an explicit device and coerce every caller-supplied tensor onto it.

@pytest.mark.requires_gpu
def test_build_packed_sequence_gpu_text_token_tags_no_longer_crashes():
    # Tiny (bytes, not layers) per the shared-GPU rule: num_text_tokens=2,
    # a single 2x2 latent frame, one audio latent, no keyframes.
    text_token_tags = torch.zeros(2, dtype=torch.long, device="cuda")
    layout = build_packed_sequence(
        text_token_tags, num_latent_frames=1, latent_height=2, latent_width=2,
        num_audio_latents=1, patch_size=PATCH,
    )
    assert layout.position_ids.device.type == "cuda"
    assert layout.token_tags.device.type == "cuda"
    assert layout.video_indices.device.type == "cuda"
    assert layout.audio_indices.device.type == "cuda"
    assert layout.text_indices.device.type == "cuda"


@pytest.mark.requires_gpu
def test_build_row_timesteps_gpu_indices_no_longer_crash():
    video_indices = torch.tensor([0, 1], device="cuda")
    audio_indices = torch.tensor([2], device="cuda")
    unique_ts, ts_idx = build_row_timesteps(
        video_indices, audio_indices, num_condition_video_rows=0, num_condition_audio_rows=0,
        num_text_tokens=0, video_timestep=0.4, audio_timestep=0.7,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    assert unique_ts.device.type == "cuda"
    assert ts_idx.device.type == "cuda"


def test_build_packed_sequence_device_defaults_to_text_token_tags_device():
    # CPU-only coercion-path test: `device=None` (the default) must derive
    # the layout's device from `text_token_tags` itself, not silently fall
    # back to a bare-CPU internal default regardless of what was passed in.
    text_token_tags = torch.zeros(2, dtype=torch.long)  # CPU
    layout = build_packed_sequence(
        text_token_tags, num_latent_frames=1, latent_height=2, latent_width=2,
        num_audio_latents=1, patch_size=PATCH,
    )
    assert layout.position_ids.device == torch.device("cpu")
    assert layout.token_tags.device == torch.device("cpu")


def test_build_packed_sequence_coerces_text_token_tags_dtype_and_device():
    # Explicit `device="cpu"` + an input dtype OTHER than long: proves the
    # coercion line (`text_token_tags.to(device=device, dtype=torch.long)`)
    # actually runs rather than being skipped because the input already
    # happened to match -- the surviving signal on a CPU-only box, where a
    # real cross-device mismatch can't be constructed.
    text_token_tags = torch.zeros(3, dtype=torch.int32)
    layout = build_packed_sequence(
        text_token_tags, num_latent_frames=1, latent_height=2, latent_width=2,
        num_audio_latents=1, patch_size=PATCH, device="cpu",
    )
    assert layout.token_tags.dtype == torch.long
    assert layout.token_tags.device == torch.device("cpu")


def test_build_packed_sequence_explicit_device_overrides_input_device():
    # Explicit `device=` always wins over `text_token_tags`'s own device --
    # the exact behavior `generator/video_minimax_h3/main.py` relies on
    # (passes `device=c.device` unconditionally).
    text_token_tags = torch.zeros(2, dtype=torch.long)  # CPU
    layout = build_packed_sequence(
        text_token_tags, num_latent_frames=1, latent_height=2, latent_width=2,
        num_audio_latents=1, patch_size=PATCH, device=torch.device("cpu"),
    )
    assert layout.position_ids.device == torch.device("cpu")


# -- ref2va: build_ref2va_packed_sequence -------------------------------------


def _fake_video_latent(num_latent_frames: int, height: int, width: int) -> torch.Tensor:
    return torch.zeros(1, 24, num_latent_frames, height, width)


def _fake_audio_rows(num_audio_latents: int, audio_channels: int = 2, channels: int = 32) -> torch.Tensor:
    return torch.zeros(num_audio_latents * audio_channels, channels)


def test_ref2va_single_image_reference_is_a_prefix_at_the_text_boundary():
    num_text_tokens = 5
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    condition_latents = [_fake_video_latent(1, 4, 4)]  # rows_per_frame = 2*2 = 4
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="image"),), condition_latents, (),
        num_latent_frames=2, latent_height=4, latent_width=4, num_audio_latents=3, patch_size=PATCH,
    )
    assert layout.num_condition_video_rows == 4
    assert layout.num_condition_audio_rows == 0
    ref_rows = layout.video_indices[: layout.num_condition_video_rows]
    assert torch.equal(ref_rows, torch.arange(num_text_tokens, num_text_tokens + 4))
    assert torch.all(layout.position_ids[ref_rows, 0] == float(num_text_tokens))
    # The reference's own vision-block rows are tagged VIDEO, its label text
    # (part of text_token_tags, not this function's concern) stays TEXT.
    assert torch.all(layout.token_tags[ref_rows] == VIDEO_TAG)
    # An image is a single rotary instant: the target starts exactly one unit
    # after the reference, regardless of the reference's own row/frame count.
    assert layout.media_rotary_origin == float(num_text_tokens) + 1.0


def test_bite_check_image_reference_advances_by_exactly_one_not_its_own_size():
    # BITE CHECK: the first reference is deliberately large (rows_per_frame
    # 16) so that a wrong implementation advancing the clock by its own row
    # or latent-frame count -- instead of the fixed 1.0 -- would move the
    # SECOND reference far away from `num_text_tokens + 1.0`.
    num_text_tokens = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    large = _fake_video_latent(1, 8, 8)   # rows_per_frame = 4*4 = 16
    small = _fake_video_latent(1, 2, 2)   # rows_per_frame = 1*1 = 1
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="image"), ReferenceBlock(kind="image")), [large, small], (),
        num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
    )
    second_ref_row = num_text_tokens + 16  # first reference's 16 rows precede it
    correct_time = float(num_text_tokens) + 1.0
    wrong_time_by_row_count = float(num_text_tokens) + 16.0
    assert float(layout.position_ids[second_ref_row, 0]) == correct_time
    assert float(layout.position_ids[second_ref_row, 0]) != wrong_time_by_row_count


def test_ref2va_standalone_audio_reference_pins_to_the_target_width_grid():
    num_text_tokens = 2
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    audio_rows = _fake_audio_rows(3)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="audio"),), (), (audio_rows,),
        num_latent_frames=1, latent_height=4, latent_width=8, num_audio_latents=2, patch_size=PATCH,
    )
    assert layout.num_condition_audio_rows == 6
    ref_rows = layout.audio_indices[: layout.num_condition_audio_rows]
    assert torch.equal(ref_rows, torch.arange(num_text_tokens, num_text_tokens + 6))
    ref_pos = layout.position_ids[ref_rows]
    assert torch.all(ref_pos[:, 1] == 0.0)  # no height coordinate
    left, right = ref_pos[:3], ref_pos[3:]
    assert torch.all(left[:, 2] == left[0, 2]) and torch.all(right[:, 2] == right[0, 2])
    # The standalone-audio-reference rule pins to the TARGET width grid --
    # the same grid the target audio block itself uses.
    target_audio_rows = layout.audio_indices[layout.num_condition_audio_rows:]
    target_pos = layout.position_ids[target_audio_rows]
    assert left[0, 2] == target_pos[0, 2]
    assert right[0, 2] == target_pos[-1, 2]
    # 3 reference audio latents advance the clock by exactly 3 units.
    assert layout.media_rotary_origin == float(num_text_tokens) + 3.0


def test_ref2va_video_reference_soundtrack_immediately_precedes_its_video_rows():
    num_text_tokens = 2
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    video_latent = _fake_video_latent(2, 4, 4)  # rows_per_frame = 4, 2 latent frames -> 8 rows
    audio_rows = _fake_audio_rows(3)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="video", has_audio=True),), (video_latent,), (audio_rows,),
        num_latent_frames=1, latent_height=4, latent_width=4, num_audio_latents=1, patch_size=PATCH,
    )
    assert layout.num_condition_audio_rows == 6
    assert layout.num_condition_video_rows == 8
    audio_ref_rows = layout.audio_indices[: layout.num_condition_audio_rows]
    video_ref_rows = layout.video_indices[: layout.num_condition_video_rows]
    assert torch.equal(audio_ref_rows, torch.arange(num_text_tokens, num_text_tokens + 6))
    assert torch.equal(video_ref_rows, torch.arange(num_text_tokens + 6, num_text_tokens + 14))
    # Rotary-aligned: the soundtrack's own origin equals the video block's own start time.
    assert float(layout.position_ids[audio_ref_rows[0], 0]) == float(num_text_tokens)
    assert float(layout.position_ids[video_ref_rows[0], 0]) == float(num_text_tokens)


def test_ref2va_video_reference_soundtrack_uses_its_OWN_width_grid_not_the_targets():
    # Unlike a standalone audio reference (target grid) or the target audio
    # block itself, a VIDEO reference's soundtrack is pinned to that
    # reference's OWN width grid -- distinguishable here because the
    # reference's own width (8) differs from the target's (4).
    num_text_tokens = 1
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    video_latent = _fake_video_latent(1, 4, 8)
    audio_rows = _fake_audio_rows(1)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="video", has_audio=True),), (video_latent,), (audio_rows,),
        num_latent_frames=1, latent_height=4, latent_width=4, num_audio_latents=1, patch_size=PATCH,
    )
    ref_audio_pos = layout.position_ids[layout.audio_indices[: layout.num_condition_audio_rows]]
    target_audio_pos = layout.position_ids[layout.audio_indices[layout.num_condition_audio_rows :]]
    assert float(ref_audio_pos[0, 2]) != float(target_audio_pos[0, 2])
    assert float(ref_audio_pos[1, 2]) != float(target_audio_pos[-1, 2])


def test_ref2va_video_reference_without_audio_has_no_soundtrack_rows():
    num_text_tokens = 2
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    video_latent = _fake_video_latent(1, 4, 4)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="video", has_audio=False),), (video_latent,), (),
        num_latent_frames=1, latent_height=4, latent_width=4, num_audio_latents=1, patch_size=PATCH,
    )
    assert layout.num_condition_audio_rows == 0
    assert layout.num_condition_video_rows == 4
    video_ref_rows = layout.video_indices[: layout.num_condition_video_rows]
    assert torch.equal(video_ref_rows, torch.arange(num_text_tokens, num_text_tokens + 4))


def test_ref2va_video_reference_advances_by_max_of_audio_latents_and_video_span():
    # video_span (1 latent frame at PATCH -> ROPE_FRAME_RESCALE=5/3) is BELOW
    # reference_audio_latents=10 here, so the audio side wins the max().
    num_text_tokens = 1
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    video_latent = _fake_video_latent(1, 2, 2)
    audio_rows = _fake_audio_rows(10)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="video", has_audio=True), ReferenceBlock(kind="image")),
        (video_latent, _fake_video_latent(1, 2, 2)), (audio_rows,),
        num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
    )
    second_ref_row = num_text_tokens + 20 + 1  # 20 audio rows + 1 video row precede the second reference
    assert float(layout.position_ids[second_ref_row, 0]) == float(num_text_tokens) + 10.0


def test_ref2va_video_reference_span_uses_pythons_sum_not_pairwise_sum():
    # Bite-check counterpart to the fl2va "last"-anchor pairwise test above:
    # `build_ref2va_packed_sequence`'s video_span is Python's builtin
    # `sum()` over the per-latent-frame span series -- the reference's exact
    # algorithm, reproduced verbatim -- which diverges from numpy's pairwise
    # sum from 16 latent frames on. NOTE: `sum()` over floats is NOT a naive
    # term-by-term accumulation on Python 3.12+ (it uses Neumaier/compensated
    # summation internally), so the independent reference here calls the
    # SAME builtin rather than reimplementing a manual loop -- a manual loop
    # would silently diverge from `sum()` itself, not just from `layout.py`.
    def python_sum(n: int) -> float:
        return sum(_FRAME_RESCALE * _FRAMES_PER_LATENT[i % len(_FRAMES_PER_LATENT)] for i in range(n))

    n = next(k for k in range(16, 40) if _ref_pairwise_span_sum(k) != python_sum(k))
    num_text_tokens = 1
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    video_latent = _fake_video_latent(n, 2, 2)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="video", has_audio=False), ReferenceBlock(kind="image")),
        (video_latent, _fake_video_latent(1, 2, 2)), (),
        num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
    )
    second_ref_row = num_text_tokens + n  # n video rows (rows_per_frame=1) precede it, no audio
    expected_time = float(num_text_tokens) + python_sum(n)
    pairwise_time = float(num_text_tokens) + _ref_pairwise_span_sum(n)
    assert expected_time != pairwise_time
    assert float(layout.position_ids[second_ref_row, 0]) == expected_time


def test_ref2va_invalid_kind_rejected():
    text_tags = torch.zeros(1, dtype=torch.long)
    with pytest.raises(ValueError):
        build_ref2va_packed_sequence(
            text_tags, (ReferenceBlock(kind="text"),), (_fake_video_latent(1, 2, 2),), (),
            num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
        )


def test_ref2va_rows_tile_the_whole_sequence_exactly_once():
    num_text_tokens = 3
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_ref2va_packed_sequence(
        text_tags, (ReferenceBlock(kind="image"), ReferenceBlock(kind="audio")),
        (_fake_video_latent(1, 2, 2),), (_fake_audio_rows(2),),
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3, patch_size=PATCH,
    )
    every = torch.cat([layout.text_indices, layout.audio_indices, layout.video_indices]).sort().values
    assert torch.equal(every, torch.arange(layout.position_ids.shape[0]))


def test_ref2va_no_references_reduces_to_text_plus_target_only():
    num_text_tokens = 4
    text_tags = torch.full((num_text_tokens,), TEXT_TAG, dtype=torch.long)
    layout = build_ref2va_packed_sequence(
        text_tags, (), (), (),
        num_latent_frames=2, latent_height=2, latent_width=2, num_audio_latents=3, patch_size=PATCH,
    )
    assert layout.num_condition_video_rows == 0
    assert layout.num_condition_audio_rows == 0
    assert layout.media_rotary_origin == float(num_text_tokens)
    ref = _ref_build_packed_sequence(
        num_text_tokens, num_latent_frames=2, latent_h=2, latent_w=2, num_audio_latents=3,
    )
    np.testing.assert_allclose(layout.position_ids.numpy(), ref["position_ids"], rtol=0, atol=0)


def test_ref2va_device_defaults_to_text_token_tags_device():
    text_token_tags = torch.zeros(2, dtype=torch.long)  # CPU
    layout = build_ref2va_packed_sequence(
        text_token_tags, (ReferenceBlock(kind="image"),), (_fake_video_latent(1, 2, 2),), (),
        num_latent_frames=1, latent_height=2, latent_width=2, num_audio_latents=1, patch_size=PATCH,
    )
    assert layout.position_ids.device == torch.device("cpu")
    assert layout.token_tags.device == torch.device("cpu")


def test_build_row_timesteps_coerces_audio_indices_onto_video_indices_device():
    # CPU-only coercion-path proof for build_row_timesteps: video_indices'
    # device is authoritative; audio_indices is coerced onto it explicitly
    # rather than assumed to already agree (cheap insurance even though both
    # always come from the same PackedLayout in practice).
    video_indices = torch.tensor([0, 1])
    audio_indices = torch.tensor([2])
    unique_ts, ts_idx = build_row_timesteps(
        video_indices, audio_indices, num_condition_video_rows=0, num_condition_audio_rows=0,
        num_text_tokens=0, video_timestep=0.4, audio_timestep=0.7,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    assert unique_ts.device == video_indices.device
    assert ts_idx.device == video_indices.device
