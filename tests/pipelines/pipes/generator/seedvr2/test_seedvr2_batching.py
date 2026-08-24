"""Temporal-batching math for the SeedVR2 video path (pure numpy, no torch/GPU).

Covers the invariants the causal-video VAE and the reference node rely on:
4n+1 snapping, sliding windows with overlap, reversed-frame padding, the
overlap cross-fade, and end-to-end stitch bookkeeping (frame counts preserved,
prepend removal handled by the caller).
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.generator.seedvr2 import batching as B


def _frames(n: int, h: int = 4, w: int = 4) -> list:
    """n distinguishable uint8 frames: frame i is constant-valued i."""
    return [np.full((h, w, 3), i, dtype=np.uint8) for i in range(n)]


# -- 4n+1 geometry ---------------------------------------------------------


# 3 is equidistant from 1 and 5; round-half-even resolves the tie down to 1.
@pytest.mark.parametrize("raw,snapped", [(0, 1), (1, 1), (2, 1), (3, 1), (4, 5),
                                         (5, 5), (6, 5), (7, 9), (9, 9), (12, 13)])
def test_snap_batch_size(raw, snapped):
    assert B.snap_batch_size(raw) == snapped
    assert B.snap_batch_size(raw) % 4 == 1


@pytest.mark.parametrize("t,pad", [(1, 0), (5, 0), (9, 0), (2, 3), (3, 2), (4, 1),
                                   (6, 3), (8, 1), (0, 1)])
def test_pad_to_4n1_count(t, pad):
    assert B.pad_to_4n1_count(t) == pad
    if t > 0:
        assert (t + pad) % 4 == 1


# -- batch windows ---------------------------------------------------------


def test_plan_batches_no_overlap():
    windows, overlap = B.plan_batches(12, 5, 0)
    assert overlap == 0
    assert windows == [(0, 5), (5, 10), (10, 12)]


def test_plan_batches_with_overlap():
    windows, overlap = B.plan_batches(13, 5, 2)
    assert overlap == 2
    # step = 3; every batch reuses the previous 2 frames.
    assert windows[0] == (0, 5)
    assert all(w[0] == prev[0] + 3 for prev, w in zip(windows, windows[1:]))
    assert windows[-1][1] == 13
    # Full coverage, no gaps.
    covered = set()
    for s, e in windows:
        covered.update(range(s, e))
    assert covered == set(range(13))


def test_plan_batches_overlap_ge_batch_resets_to_zero():
    windows, overlap = B.plan_batches(10, 5, 5)
    assert overlap == 0
    assert windows == [(0, 5), (5, 10)]


def test_plan_batches_drops_tail_window_that_only_recovers_overlap():
    # 7 frames, batch 5, overlap 2 -> windows (0,5), (3,7); a next start at 6
    # would cover only (6,7) <= overlap and must be dropped.
    windows, _ = B.plan_batches(7, 5, 2)
    assert windows == [(0, 5), (3, 7)]


def test_plan_batches_single_short_clip():
    windows, _ = B.plan_batches(3, 5, 0)
    assert windows == [(0, 3)]


# -- reversed padding ------------------------------------------------------


def test_pad_reversed_tail_mirrors_interior():
    frames = _frames(5)
    padded = B.pad_reversed(frames, 2, prepend=False)
    assert len(padded) == 7
    # Tail should mirror just inside the boundary: ..., 3, 4, 3, 2
    assert int(padded[5][0, 0, 0]) == 3
    assert int(padded[6][0, 0, 0]) == 2


def test_pad_reversed_prepend_mirrors_head():
    frames = _frames(5)
    padded = B.pad_reversed(frames, 2, prepend=True)
    assert len(padded) == 7
    # Head mirror: 2, 1, 0, 1, 2, ...
    assert int(padded[0][0, 0, 0]) == 2
    assert int(padded[1][0, 0, 0]) == 1
    assert int(padded[2][0, 0, 0]) == 0


def test_pad_reversed_overflow_never_raises():
    frames = _frames(2)
    padded = B.pad_reversed(frames, 10, prepend=False)
    assert len(padded) == 12
    padded = B.pad_reversed(frames, 10, prepend=True)
    assert len(padded) == 12


def test_pad_batch_uniform_fills_to_batch_size():
    frames = _frames(3)
    padded, true_len = B.pad_batch(frames, 9, uniform=True)
    assert true_len == 3
    assert len(padded) == 9


def test_pad_batch_non_uniform_fills_to_next_4n1():
    frames = _frames(3)
    padded, true_len = B.pad_batch(frames, 9, uniform=False)
    assert true_len == 3
    assert len(padded) == 5
    assert len(padded) % 4 == 1


# -- overlap blend / stitch --------------------------------------------------


def test_overlap_blend_weights_shapes_and_range():
    assert B.overlap_blend_weights(0).shape == (0,)
    w2 = B.overlap_blend_weights(2)
    assert np.allclose(w2, [1.0, 0.0])
    w5 = B.overlap_blend_weights(5)
    assert w5.shape == (5,)
    assert w5[0] == pytest.approx(1.0) and w5[-1] == pytest.approx(0.0)
    assert np.all(np.diff(w5) <= 1e-6)  # monotonically non-increasing


def test_blend_overlap_midpoint():
    a = [np.full((2, 2, 3), 100, dtype=np.uint8)] * 3
    b = [np.full((2, 2, 3), 200, dtype=np.uint8)] * 3
    blended = B.blend_overlap(a, b, 3)
    # First frame ~ previous batch, last frame ~ current batch.
    assert int(blended[0][0, 0, 0]) == 100
    assert int(blended[-1][0, 0, 0]) == 200


def test_stitch_batches_no_overlap_is_concat():
    fb = [_frames(5), _frames(3)]
    out = B.stitch_batches(fb, 0)
    assert len(out) == 8


def test_stitch_batches_overlap_consumes_shared_region():
    # Two batches sharing 2 frames: 5 + 5 with overlap 2 -> 8 unique frames.
    first = _frames(5)
    second = _frames(5)
    out = B.stitch_batches([first, second], 2)
    assert len(out) == 8


def test_video_pipeline_bookkeeping_end_to_end():
    """plan -> pad -> (identity restore) -> trim -> stitch preserves frame count."""
    total, batch_size, overlap, prepend = 11, 5, 2, 2
    seq = B.pad_reversed(_frames(total), prepend, prepend=True)
    windows, eff_overlap = B.plan_batches(len(seq), batch_size, overlap)
    batch_frames = []
    for (s, e) in windows:
        padded, true_len = B.pad_batch(seq[s:e], batch_size, uniform=True)
        # identity "restore": trim back to true length like the pipe does
        batch_frames.append(padded[:true_len])
    stitched = B.stitch_batches(batch_frames, eff_overlap)
    stitched = stitched[prepend:]
    assert len(stitched) == total
