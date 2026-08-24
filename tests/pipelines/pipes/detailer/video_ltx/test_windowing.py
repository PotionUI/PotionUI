"""Pure stabilized-window geometry tests for the LTX video detailer.

Covers the fixed-vs-moving decision, uniform crop size, frame clamping, the
32px working-resolution snap, and the EMA/interp center smoothing -- the edge
cases the design calls out: one giant face, a roaming subject, boxes at the
frame edge.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes.detailer.video_ltx.tracking import Detection, Track
from src.pipelines.pipes.detailer.video_ltx.windowing import (
    ema_smooth,
    interpolate_centers,
    pad_bbox,
    snap_working_resolution,
    stabilize_window,
)


def _track(*framed_boxes, kind="face"):
    return Track([Detection(f, b, kind) for f, b in framed_boxes], kind)


# -- primitives -----------------------------------------------------------


def test_pad_bbox_scales_about_center():
    # 10x10 box centered at (5,5), 1.8x -> 18x18 centered at (5,5)
    x0, y0, x1, y1 = pad_bbox((0, 0, 10, 10), 1.8)
    assert (x0, y0, x1, y1) == pytest.approx((-4.0, -4.0, 14.0, 14.0))


def test_snap_working_resolution_upscales_small_and_snaps_32():
    tw, th = snap_working_resolution(90, 120, short_side=512)
    assert tw % 32 == 0 and th % 32 == 0
    assert min(tw, th) == pytest.approx(512, abs=32)  # short side upscaled ~512
    # a small crop must gain resolution, never lose it
    assert tw >= 90 and th >= 120


def test_snap_working_resolution_refines_large_crop_at_native_size():
    # (2026-07-16): the maintainer's ~450px face padded 1.8x lands
    # a ~810px window -- inside [512, 1024], so it refines at (snapped) NATIVE
    # size, NOT downscaled to 512 (the bug the first A/B exposed).
    tw, th = snap_working_resolution(810, 780, short_side=512, max_short_side=1024)
    assert tw % 32 == 0 and th % 32 == 0
    # working size is native-or-larger (snapped UP), never below the crop
    assert tw >= 810 and th >= 780
    # and it was NOT collapsed to the 512 floor
    assert min(tw, th) > 512


def test_snap_working_resolution_never_below_crop_native_size_up_to_cap():
    # the load-bearing invariant: for any crop at or below the cap, both working
    # dims are >= the crop's own dims (small crops upscale, large refine native).
    for w, h in [(90, 120), (300, 300), (513, 700), (700, 513), (1024, 1024),
                 (1000, 1024), (640, 480), (128, 900)]:
        tw, th = snap_working_resolution(w, h, short_side=512, max_short_side=1024)
        assert tw >= w and th >= h, f"working {tw}x{th} fell below crop {w}x{h}"
        assert tw % 32 == 0 and th % 32 == 0


def test_snap_working_resolution_caps_oversized_crop_at_short_side():
    # the ONLY downscale regime: a crop whose short side exceeds the cap is
    # mildly scaled down so the short side lands at the cap (VRAM safety).
    tw, th = snap_working_resolution(4000, 1500, short_side=512, max_short_side=1024)
    assert tw % 32 == 0 and th % 32 == 0
    assert min(tw, th) == pytest.approx(1024, abs=32)  # short side capped ~1024
    assert min(tw, th) < 1500                          # genuinely downscaled


# -- fixed window ---------------------------------------------------------


def test_small_subject_gets_one_fixed_window():
    track = _track((0, (100, 100, 140, 140)), (6, (102, 100, 142, 140)), (12, (104, 100, 144, 140)))
    win = stabilize_window(track, 512, 512)
    assert win.moving is False
    assert win.start_frame == 0 and win.end_frame == 12
    # one box per frame (0..12 inclusive), all identical, all the window size
    assert len(win.boxes) == 13
    assert all(b == win.boxes[0] for b in win.boxes)
    assert all((b[2] - b[0], b[3] - b[1]) == (win.width, win.height) for b in win.boxes)


def test_fixed_window_covers_union_padded():
    track = _track((0, (100, 100, 140, 140)), (6, (140, 100, 180, 140)))
    win = stabilize_window(track, 512, 512, pad_factor=1.8)
    x0, y0, x1, y1 = win.boxes[0]
    # window must contain the union (100..180 x 100..140)
    assert x0 <= 100 and x1 >= 180 and y0 <= 100 and y1 >= 140


# -- moving window --------------------------------------------------------


def test_large_roaming_subject_uses_moving_window():
    # subject sweeps across most of the frame -> union > 40% area -> moving
    track = _track(
        (0, (0, 200, 120, 320)),
        (30, (200, 200, 320, 320)),
        (60, (380, 200, 500, 320)),
    )
    win = stabilize_window(track, 512, 512, area_threshold=0.40)
    assert win.moving is True
    # uniform size, and the center translates rightward following the subject
    # (deliberately LAGGED by the heavy EMA smoothing -- it follows the
    # direction of travel, it does not snap to each detection).
    sizes = {(b[2] - b[0], b[3] - b[1]) for b in win.boxes}
    assert sizes == {(win.width, win.height)}
    first_cx = (win.boxes[0][0] + win.boxes[0][2]) / 2
    last_cx = (win.boxes[-1][0] + win.boxes[-1][2]) / 2
    assert last_cx > first_cx  # window followed the subject rightward


def test_moving_window_boxes_stay_inside_frame():
    track = _track((0, (0, 200, 140, 340)), (30, (372, 200, 512, 340)))
    win = stabilize_window(track, 512, 512, area_threshold=0.40)
    for x0, y0, x1, y1 in win.boxes:
        assert 0 <= x0 and 0 <= y0 and x1 <= 512 and y1 <= 512
        assert (x1 - x0, y1 - y0) == (win.width, win.height)  # size never clipped


def test_one_giant_face_does_not_crash_and_stays_in_frame():
    # a single detection larger than 40% of the frame
    track = _track((0, (20, 20, 500, 500)), (6, (20, 20, 500, 500)))
    win = stabilize_window(track, 512, 512, area_threshold=0.40)
    assert win.width <= 512 and win.height <= 512
    for x0, y0, x1, y1 in win.boxes:
        assert 0 <= x0 and x1 <= 512 and 0 <= y0 and y1 <= 512


# -- center smoothing -----------------------------------------------------


def test_ema_smooth_lags_a_step():
    out = ema_smooth([0.0, 10.0, 10.0, 10.0], alpha=0.5)
    assert out[0] == 0.0
    assert out[1] == 5.0  # 0.5*10 + 0.5*0
    assert out[-1] > out[1]  # converges toward 10


def test_interpolate_centers_linear_between_samples():
    centers = interpolate_centers([0, 10], [(0.0, 0.0), (10.0, 20.0)], range(0, 11))
    assert centers[0] == (0.0, 0.0)
    assert centers[5] == pytest.approx((5.0, 10.0))
    assert centers[10] == (10.0, 20.0)


def test_box_at_indexes_by_absolute_frame():
    track = _track((5, (100, 100, 140, 140)), (11, (100, 100, 140, 140)))
    win = stabilize_window(track, 512, 512)
    assert win.box_at(5) == win.boxes[0]
    assert win.box_at(11) == win.boxes[-1]
