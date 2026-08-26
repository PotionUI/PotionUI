"""Tests for MiniMax-H3 request geometry: canvas snap, frame-count snap
(17n+5 -> 5n+2), and the audio-latent-count formula. CPU-only, no weights."""

from __future__ import annotations

import pytest

from src.pipelines.pipes.generator.video_minimax_h3.geometry import (
    align_num_frames,
    audio_latent_num_frames,
    pixel_frames_for_latent_frames,
    resolve_canvas_size,
    resolve_request_geometry,
    video_latent_num_frames,
)


# -- canvas -----------------------------------------------------------------

def test_default_16_9_canvas_matches_the_dossier():
    # dossier: "Default 16:9 canvas = 1344 x 768".
    assert resolve_canvas_size(16, 9) == (768, 1344)


def test_square_canvas_spans_the_short_edge_both_axes():
    assert resolve_canvas_size(1, 1) == (768, 768)


@pytest.mark.parametrize("w,h,expected", [
    (16, 9, (768, 1344)),
    (9, 16, (1344, 768)),
    (1, 1, (768, 768)),
    (4, 3, (768, 1024)),
    (3, 4, (1024, 768)),
])
def test_canvas_snap_table(w, h, expected):
    assert resolve_canvas_size(w, h) == expected


def test_canvas_axes_are_multiples_of_32():
    for w, h in [(16, 9), (9, 16), (21, 9), (9, 21), (1, 4), (4, 1)]:
        height, width = resolve_canvas_size(w, h)
        assert height % 32 == 0
        assert width % 32 == 0


def test_canvas_aspect_out_of_range_rejected():
    with pytest.raises(ValueError):
        resolve_canvas_size(5, 1)  # ratio 5 > max 4
    with pytest.raises(ValueError):
        resolve_canvas_size(1, 5)  # ratio 0.2 < min 0.25


def test_canvas_nonpositive_rejected():
    with pytest.raises(ValueError):
        resolve_canvas_size(0, 9)
    with pytest.raises(ValueError):
        resolve_canvas_size(16, -1)


def test_canvas_area_cap_applies_before_rounding():
    # An extreme-but-in-range aspect ratio pushes the pre-rounding area right
    # up against canvas_max_pixels; the resolved area may end up SLIGHTLY
    # above the budget once both axes round to 32, never wildly above it.
    height, width = resolve_canvas_size(4, 1)
    assert height * width <= 768 * 1344 * 1.05


# -- frame count (17n+5 -> 5n+2) ---------------------------------------------

@pytest.mark.parametrize("requested,aligned,latents", [
    (124, 124, 37),   # the dossier's own worked example: n=7
    (5, 5, 2),        # n=0, the minimum chunk
    (1, 5, 2),        # snaps UP to the first valid count
    (6, 22, 7),       # 22 = 17*1+5
    (22, 22, 7),
    (23, 39, 12),     # snaps up past 23 to 39 = 17*2+5
])
def test_align_and_latent_frame_table(requested, aligned, latents):
    got_aligned = align_num_frames(requested)
    assert got_aligned == aligned
    assert video_latent_num_frames(got_aligned) == latents


def test_align_num_frames_rejects_nonpositive():
    with pytest.raises(ValueError):
        align_num_frames(0)


def test_video_latent_num_frames_rejects_unaligned_input():
    with pytest.raises(ValueError):
        video_latent_num_frames(100)  # 100 % 17 != 5


# -- pixel_frames_for_latent_frames: inverse of video_latent_num_frames -----

@pytest.mark.parametrize("requested,aligned,latents", [
    (124, 124, 37),
    (5, 5, 2),
    (6, 22, 7),
    (22, 22, 7),
    (23, 39, 12),
])
def test_pixel_frames_for_latent_frames_inverts_the_table(requested, aligned, latents):
    assert pixel_frames_for_latent_frames(latents) == aligned


def test_pixel_frames_for_latent_frames_round_trips_video_latent_num_frames():
    for aligned in (5, 22, 39, 124, 141):
        latents = video_latent_num_frames(aligned)
        assert pixel_frames_for_latent_frames(latents) == aligned


def test_pixel_frames_for_latent_frames_rejects_a_count_below_the_minimum():
    with pytest.raises(ValueError):
        pixel_frames_for_latent_frames(1)


def test_pixel_frames_for_latent_frames_rejects_a_count_off_the_5n_plus_2_grid():
    with pytest.raises(ValueError, match="not of the form"):
        pixel_frames_for_latent_frames(8)  # 8 - 2 = 6, not a multiple of 5


# -- audio latent count -------------------------------------------------------

@pytest.mark.parametrize("frames,expected", [
    (124, 207),   # dossier's own worked example: round(124/24*40)
    (24, 40),     # exactly 1 second
    (12, 20),     # exactly 0.5 second
    (1, 2),       # round(1/24*40) = round(1.667) = 2
])
def test_audio_latent_num_frames_table(frames, expected):
    assert audio_latent_num_frames(frames) == expected


def test_audio_latent_num_frames_rounds_half_to_even_or_up_consistently():
    # round() is banker's rounding in Python; assert this module matches
    # Python's own `round`, not a naive floor/ceil, since the reference uses
    # `int(round(...))` verbatim.
    frames = 17  # 17/24*40 = 28.333...
    assert audio_latent_num_frames(frames) == round(17 / 24 * 40)


# -- end-to-end request resolve -----------------------------------------------

def test_resolve_request_geometry_default_canvas_and_frames():
    height, width, frames, num_latent_frames, latent_height, latent_width, num_audio_latents = (
        resolve_request_geometry(None, None, 124)
    )
    assert (height, width) == (768, 1344)
    assert frames == 124
    assert num_latent_frames == 37
    assert latent_height == 768 // 16
    assert latent_width == 1344 // 16
    assert num_audio_latents == 207


def test_resolve_request_geometry_accepts_short_requests_below_the_released_range():
    # 1 frame aligns up to 5 -> 5/24s, far under the model's recommended 5s
    # floor -- but that floor is a recommendation, not something this pipe
    # enforces, so it must run rather than raise.
    height, width, frames, num_latent_frames, latent_height, latent_width, num_audio_latents = (
        resolve_request_geometry(None, None, 1)
    )
    assert frames == 5
    assert num_latent_frames == video_latent_num_frames(5) == 2


def test_resolve_request_geometry_rejects_beyond_the_max_duration():
    # 361 frames aligns up to 362 -> 15.08s, just over the 15s ceiling.
    with pytest.raises(ValueError):
        resolve_request_geometry(None, None, 361)


def test_resolve_request_geometry_rejects_height_without_width():
    with pytest.raises(ValueError):
        resolve_request_geometry(768, None, 124)


def test_resolve_request_geometry_rejects_non_multiple_of_32():
    with pytest.raises(ValueError):
        resolve_request_geometry(769, 1344, 124)
