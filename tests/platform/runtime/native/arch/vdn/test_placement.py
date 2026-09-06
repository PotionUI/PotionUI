"""The VDN block's transient reserve: the larger of the two branches, at the peak."""

from __future__ import annotations

from src.platform.runtime.native.arch.minimax_h3.vdn import (
    VdnLayout,
    estimate_vdn_transient_gb,
    linear_branch_transient_breakdown,
    vdn_transient_breakdown,
    window_bounds,
)
from src.platform.runtime.native.arch.minimax_h3.vdn.window import estimate_window_transient_gb

# The released 768p / 14.4s shape: 102 latent frames of a 36x28 patched grid, 56 heads
# of 128, hidden 5376, ~2000 global rows ahead of the video block.
FRAMES = 102
GRID = (36, 28)
TOKENS_PER_FRAME = GRID[0] * GRID[1]
HEADS = 56
HEAD_DIM = 128
HIDDEN = 5376
GLOBAL_ROWS = 2000


def _released_layout() -> VdnLayout:
    return VdnLayout(
        seq_len=GLOBAL_ROWS + FRAMES * TOKENS_PER_FRAME,
        video_start=GLOBAL_ROWS,
        num_frames=FRAMES,
        tokens_per_frame=TOKENS_PER_FRAME,
        frame_height=GRID[0],
        frame_width=GRID[1],
        window_bounds=tuple(window_bounds(FRAMES)),
        text_start=0,
        text_len=512,
    ).with_anchor_mode("both")


def test_the_breakdown_is_the_two_branches_own_estimates():
    layout = _released_layout()
    breakdown = vdn_transient_breakdown(layout, HEADS, HEAD_DIM, HIDDEN)
    branch = linear_branch_transient_breakdown(
        layout.branch_frames, HEADS, HEAD_DIM, TOKENS_PER_FRAME, HIDDEN
    )

    assert breakdown.window_gb == estimate_window_transient_gb(layout, HEADS, HEAD_DIM)
    assert breakdown.scan_gb == branch.scan_gb
    assert breakdown.readout_gb == branch.readout_gb


def test_the_reserve_is_the_epilogue_peak_not_the_scan():
    layout = _released_layout()
    breakdown = vdn_transient_breakdown(layout, HEADS, HEAD_DIM, HIDDEN)

    # The epilogue is the branch's larger phase at this shape, and larger than the
    # widest window group -- a reserve taken from the scan figure would OOM on it.
    assert breakdown.readout_gb > breakdown.scan_gb
    assert breakdown.readout_gb > breakdown.window_gb
    assert estimate_vdn_transient_gb(layout, HEADS, HEAD_DIM, HIDDEN) == breakdown.readout_gb


def test_the_branch_is_costed_on_the_frames_it_actually_scans():
    layout = _released_layout()
    assert layout.branch_frames == FRAMES - 2

    with_anchors = vdn_transient_breakdown(layout, HEADS, HEAD_DIM, HIDDEN)
    without = vdn_transient_breakdown(
        layout.with_anchor_mode("none"), HEADS, HEAD_DIM, HIDDEN
    )

    assert with_anchors.scan_gb < without.scan_gb
