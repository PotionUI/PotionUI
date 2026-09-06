"""The VDN windowed softmax branch: window geometry, exactness, grouping, gate."""

from __future__ import annotations

import math

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.minimax_h3.vdn import window as window_mod
from src.platform.runtime.native.arch.minimax_h3.vdn.layout import VdnLayout
from src.platform.runtime.native.arch.minimax_h3.vdn.window import (
    ANCHOR_MODES,
    build_softmax_gate,
    clear_window_plan_cache,
    covers_all_frames,
    estimate_window_transient_gb,
    softmax_gate,
    window_bounds,
    windowed_softmax,
)

HEADS = 2
HEAD_DIM = 8
TOKENS_PER_FRAME = 4
TEXT_LEN = 3
EXTRA_GLOBAL_ROWS = 2


@pytest.fixture(autouse=True)
def _fresh_plan_cache():
    clear_window_plan_cache()
    yield
    clear_window_plan_cache()


def make_layout(num_frames: int, chunk: int = 5, radius: int = 1) -> VdnLayout:
    """Text, then two other global rows, then the target video block at the end."""
    video_start = TEXT_LEN + EXTRA_GLOBAL_ROWS
    return VdnLayout(
        seq_len=video_start + num_frames * TOKENS_PER_FRAME,
        video_start=video_start,
        num_frames=num_frames,
        tokens_per_frame=TOKENS_PER_FRAME,
        frame_height=2,
        frame_width=2,
        window_bounds=tuple(window_bounds(num_frames, chunk=chunk, radius=radius)),
        text_start=0,
        text_len=TEXT_LEN,
    )


def make_qkv(layout: VdnLayout, seed: int = 0):
    generator = torch.Generator().manual_seed(seed)
    shape = (layout.seq_len, HEADS, HEAD_DIM)
    return tuple(torch.randn(shape, generator=generator, dtype=torch.float32) for _ in range(3))


def keep_matrix(layout: VdnLayout, anchor_frames: str) -> torch.Tensor:
    """The mask, written out from its definition:

        keep(q, kv) = not (q_is_video and kv_is_video) or inside_window
    """
    total = layout.seq_len
    frames, tokens = layout.num_frames, layout.tokens_per_frame
    video_start, video_end = layout.video_start, layout.video_end
    anchors = {0, frames - 1}
    row_anchors = anchors if anchor_frames in ("rows", "both") else set()
    column_anchors = anchors if anchor_frames in ("columns", "both") else set()

    def frame_of(row: int) -> int:
        return (row - video_start) // tokens

    keep = torch.zeros(total, total, dtype=torch.bool)
    for query in range(total):
        query_is_video = video_start <= query < video_end
        for key in range(total):
            key_is_video = video_start <= key < video_end
            if not (query_is_video and key_is_video):
                keep[query, key] = True
                continue
            query_frame, key_frame = frame_of(query), frame_of(key)
            low, high = layout.window_bounds[query_frame]
            keep[query, key] = (
                low <= key_frame <= high
                or key_frame in column_anchors
                or query_frame in row_anchors
            )
    return keep


def masked_reference(query, key, value, mask) -> torch.Tensor:
    """One masked SDPA over the whole sequence. ``[T, H, d]`` in and out."""
    out = F.scaled_dot_product_attention(
        query.permute(1, 0, 2).unsqueeze(0),
        key.permute(1, 0, 2).unsqueeze(0),
        value.permute(1, 0, 2).unsqueeze(0),
        attn_mask=mask,
    )
    return out.squeeze(0).permute(1, 0, 2)


def test_window_bounds_chunk_aligned_table():
    # F=12 at chunk 5, radius 1: frame t sits in chunk t // 5 and sees whole
    # chunks t//5 - 1 .. t//5 + 1, unclamped.
    assert window_bounds(12, chunk=5, radius=1) == [
        (-5, 9), (-5, 9), (-5, 9), (-5, 9), (-5, 9),
        (0, 14), (0, 14), (0, 14), (0, 14), (0, 14),
        (5, 19), (5, 19),
    ]


def test_window_bounds_frame_mode_is_centered():
    assert window_bounds(4, chunk=0, radius=1) == [(-1, 1), (0, 2), (1, 3), (2, 4)]


def test_window_bounds_short_sequence_covers_everything():
    bounds = window_bounds(3, chunk=5, radius=1)
    assert bounds == [(-5, 9), (-5, 9), (-5, 9)]
    assert covers_all_frames(3, bounds, "both") is True


def test_anchor_columns_alone_can_make_the_window_full_cover():
    # Frame mode radius 1 over 3 frames keeps {t-1, t, t+1}; the two anchor
    # columns close the remaining gap, so nothing is left for a linear branch.
    bounds = window_bounds(3, chunk=0, radius=1)
    assert covers_all_frames(3, bounds, "none") is False
    assert covers_all_frames(3, bounds, "both") is True


@pytest.mark.parametrize("anchor_frames", ANCHOR_MODES)
@pytest.mark.parametrize(
    "num_frames, chunk, radius",
    [
        (20, 5, 1),   # chunk-aligned
        (12, 5, 1),   # 12 = 2*5 + 2, a short final chunk
        (12, 0, 1),   # frame mode, every frame its own group
    ],
)
def test_windowed_softmax_equals_the_masked_reference(num_frames, chunk, radius, anchor_frames):
    layout = make_layout(num_frames, chunk=chunk, radius=radius)
    assert not covers_all_frames(num_frames, layout.window_bounds, anchor_frames)
    query, key, value = make_qkv(layout)

    out = windowed_softmax(
        query, key, value, layout, heads=HEADS, anchor_frames=anchor_frames
    )
    expected = masked_reference(query, key, value, keep_matrix(layout, anchor_frames))

    assert out is not None
    torch.testing.assert_close(out, expected, rtol=0, atol=1e-5)


def test_anchor_keys_inside_the_window_are_not_counted_twice():
    # At F=12, chunk 5, radius 1 the chunk-0 queries keep frames 0..9, so anchor
    # frame 0 is ALREADY in their window while anchor frame 11 is not. Gathering
    # frame 0 a second time would double its share of the softmax mass.
    num_frames = 12
    layout = make_layout(num_frames)
    low, high = layout.window_bounds[1]
    assert low <= 0 <= high and not low <= num_frames - 1 <= high

    query, key, value = make_qkv(layout, seed=3)
    out = windowed_softmax(query, key, value, layout, heads=HEADS, anchor_frames="both")
    expected = masked_reference(query, key, value, keep_matrix(layout, "both"))

    frame_one = slice(layout.video_start + TOKENS_PER_FRAME, layout.video_start + 2 * TOKENS_PER_FRAME)
    torch.testing.assert_close(out[frame_one], expected[frame_one], rtol=0, atol=1e-5)


def test_global_rows_attend_the_whole_sequence():
    num_frames = 12
    layout = make_layout(num_frames)
    query, key, value = make_qkv(layout, seed=7)

    out = windowed_softmax(query, key, value, layout, heads=HEADS, anchor_frames="both")
    dense = masked_reference(query, key, value, torch.ones(layout.seq_len, layout.seq_len, dtype=torch.bool))

    globals_ = slice(0, layout.video_start)
    torch.testing.assert_close(out[globals_], dense[globals_], rtol=0, atol=1e-5)


def test_video_queries_attend_the_global_keys():
    num_frames = 12
    layout = make_layout(num_frames)
    query, key, value = make_qkv(layout, seed=11)

    before = windowed_softmax(query, key, value, layout, heads=HEADS, anchor_frames="both")
    perturbed = value.clone()
    perturbed[layout.video_start - 1] += 10.0
    after = windowed_softmax(query, key, perturbed, layout, heads=HEADS, anchor_frames="both")

    video = slice(layout.video_start, layout.video_end)
    moved = (after[video] - before[video]).abs().amax(dim=(1, 2))
    assert bool((moved > 1e-4).all()), "a global key left some video query's output untouched"


def test_full_cover_returns_none():
    num_frames = 3
    layout = make_layout(num_frames)
    query, key, value = make_qkv(layout)

    assert windowed_softmax(
        query, key, value, layout, heads=HEADS, anchor_frames="both"
    ) is None


def test_query_rows_are_grouped_into_a_handful_of_dense_calls(monkeypatch):
    num_frames = 20
    layout = make_layout(num_frames)
    query, key, value = make_qkv(layout, seed=5)

    calls = []
    real = window_mod._dispatch_attention

    def counting(*args, **kwargs):
        calls.append(tuple(args[0].shape))
        return real(*args, **kwargs)

    monkeypatch.setattr(window_mod, "_dispatch_attention", counting)
    windowed_softmax(query, key, value, layout, heads=HEADS, anchor_frames="both")

    # One dense leg for the globals and anchor rows, then one call per chunk.
    assert len(calls) <= math.ceil(num_frames / 5) + 2, calls
    assert len(calls) < num_frames


def test_transient_estimate_counts_the_widest_group():
    layout = make_layout(20)
    estimate = estimate_window_transient_gb(
        layout, HEADS, HEAD_DIM, anchor_frames="both", bytes_per_element=2
    )
    # Widest group: 15 window frames plus anchor frame 19, 5 global rows, and
    # 5 query frames -- (2*69 + 2*20) * 2 heads * 8 dims * 2 bytes.
    assert estimate == pytest.approx((2 * 69 + 2 * 20) * HEADS * HEAD_DIM * 2 / 1024 ** 3)


def test_softmax_gate_starts_at_its_init_value_and_broadcasts_per_head():
    hidden = 6
    gate = build_softmax_gate(hidden, HEADS, init_value=0.99)

    assert torch.count_nonzero(gate.weight) == 0
    assert gate.bias.detach().tolist() == pytest.approx([math.log(0.99 / 0.01)] * HEADS)

    x = torch.randn(5, hidden)
    values = softmax_gate(x, gate)
    assert values.shape == (5, HEADS, 1)
    torch.testing.assert_close(values, torch.full_like(values, 0.99), rtol=0, atol=1e-6)

    attended = torch.randn(5, HEADS, HEAD_DIM)
    torch.testing.assert_close(values * attended, attended * 0.99, rtol=0, atol=1e-6)
