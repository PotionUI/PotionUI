"""Pure tracking-math tests for the LTX video detailer.

These cover the behaviours most likely to misbehave on real footage: IoU
linking (incl. gaps and crossing subjects), scene-cut splitting, short-track
filtering, and the cap/merge. numpy-only, no detector, no model.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.detailer.video_ltx.tracking import (
    Detection,
    Track,
    cap_and_merge_tracks,
    detect_scene_cuts,
    filter_short_tracks,
    frame_histogram,
    histogram_distance,
    iou,
    link_detections,
    overlap_fraction,
    split_tracks_at_cuts,
    track_score,
    union_bbox,
)


def _det(frame, box, kind="face"):
    return Detection(frame, box, kind)


def _frames(*rows):
    """rows: (frame_idx, [box, ...]) -> link_detections input, one kind."""
    return [(f, [_det(f, b) for b in boxes]) for f, boxes in rows]


# -- geometry -------------------------------------------------------------


def test_iou_identical_and_disjoint():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_iou_half_overlap():
    # two 10x10 boxes sharing a 5x10 strip -> inter 50, union 150
    assert iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_overlap_fraction_small_box_inside_large():
    # small box fully inside a large one -> ~1.0 even though IoU is tiny
    big = (0, 0, 100, 100)
    small = (40, 40, 50, 50)
    assert overlap_fraction(big, small) == pytest.approx(1.0)
    assert iou(big, small) < 0.02


def test_union_bbox():
    assert union_bbox([(0, 0, 4, 4), (2, 3, 8, 9)]) == (0, 0, 8, 9)


# -- linking --------------------------------------------------------------


def test_link_single_subject_across_frames():
    frame_dets = _frames(
        (0, [(0, 0, 10, 10)]),
        (6, [(1, 1, 11, 11)]),
        (12, [(2, 2, 12, 12)]),
    )
    tracks = link_detections(frame_dets)
    assert len(tracks) == 1
    assert [d.frame for d in tracks[0].detections] == [0, 6, 12]


def test_link_two_separate_subjects_stay_separate():
    frame_dets = _frames(
        (0, [(0, 0, 10, 10), (50, 50, 60, 60)]),
        (6, [(1, 0, 11, 10), (51, 50, 61, 60)]),
    )
    tracks = link_detections(frame_dets)
    assert len(tracks) == 2
    # each track's boxes are the same corner cluster throughout
    for t in tracks:
        xs = [d.box[0] for d in t.detections]
        assert max(xs) - min(xs) < 5  # never jumped to the other subject


def test_link_tolerates_a_one_frame_gap():
    # subject missed at the middle stride, reappears -> still ONE track
    frame_dets = _frames(
        (0, [(0, 0, 10, 10)]),
        (6, []),
        (12, [(1, 1, 11, 11)]),
    )
    tracks = link_detections(frame_dets, max_gap_steps=1)
    assert len(tracks) == 1
    assert [d.frame for d in tracks[0].detections] == [0, 12]


def test_link_breaks_after_gap_exceeds_tolerance():
    frame_dets = _frames(
        (0, [(0, 0, 10, 10)]),
        (6, []),
        (12, []),
        (18, [(0, 0, 10, 10)]),
    )
    tracks = link_detections(frame_dets, max_gap_steps=1)
    assert len(tracks) == 2  # gap of two sampled steps severs the track


def test_link_no_detections_yields_no_tracks():
    assert link_detections([(0, []), (6, [])]) == []


def test_link_low_iou_does_not_join():
    # a subject teleports far -> two tracks, not one drifting box
    frame_dets = _frames((0, [(0, 0, 10, 10)]), (6, [(80, 80, 90, 90)]))
    tracks = link_detections(frame_dets, iou_threshold=0.3)
    assert len(tracks) == 2


# -- scene cuts -----------------------------------------------------------


def test_histogram_distance_bounds():
    a = np.zeros((8, 8, 3), np.uint8)
    b = np.full((8, 8, 3), 255, np.uint8)
    assert histogram_distance(frame_histogram(a), frame_histogram(b)) == pytest.approx(1.0)


def test_detect_scene_cuts_flags_the_hard_jump():
    dark = np.zeros((8, 8, 3), np.uint8)
    bright = np.full((8, 8, 3), 255, np.uint8)
    frames = [dark, dark, bright, bright]  # cut between index 1 and 2
    cuts = detect_scene_cuts(frames, [0, 1, 2, 3], threshold=0.35)
    assert cuts == [2]


def test_detect_scene_cuts_ignores_gradual_change():
    # a textured frame (spread histogram) brightening slightly each step -- a
    # gradual pan/exposure drift, not a shot change. A flat frame would be a
    # degenerate delta-histogram that any brightness shift moves wholesale
    # between bins; real footage has spread, so a small drift stays well under
    # threshold.
    base = np.tile(np.arange(0, 256, 4, dtype=np.uint8)[None, :, None], (8, 1, 3))  # (8,64,3) ramp
    frames = [np.clip(base.astype(np.int16) + 4 * i, 0, 255).astype(np.uint8) for i in range(4)]
    assert detect_scene_cuts(frames, [0, 1, 2, 3], threshold=0.35) == []


def test_split_tracks_at_cuts():
    track = Track([_det(0, (0, 0, 5, 5)), _det(6, (0, 0, 5, 5)), _det(12, (0, 0, 5, 5))], "face")
    out = split_tracks_at_cuts([track], cut_frames=[12])
    assert len(out) == 2
    assert [d.frame for d in out[0].detections] == [0, 6]
    assert [d.frame for d in out[1].detections] == [12]


def test_split_tracks_no_cuts_is_passthrough():
    track = Track([_det(0, (0, 0, 5, 5)), _det(6, (0, 0, 5, 5))], "face")
    out = split_tracks_at_cuts([track], cut_frames=[])
    assert len(out) == 1 and out[0] is track


# -- filter / cap / merge -------------------------------------------------


def test_filter_short_tracks_drops_brief_ones():
    long_track = Track([_det(0, (0, 0, 5, 5)), _det(30, (0, 0, 5, 5))], "face")   # 31 frames
    brief = Track([_det(0, (0, 0, 5, 5)), _det(3, (0, 0, 5, 5))], "face")         # 4 frames
    kept = filter_short_tracks([long_track, brief], min_frames=13)
    assert kept == [long_track]


def test_track_score_prefers_bigger_and_longer():
    small_long = Track([_det(0, (0, 0, 10, 10)), _det(40, (0, 0, 10, 10))], "face")
    big_short = Track([_det(0, (0, 0, 60, 60)), _det(2, (0, 0, 60, 60))], "face")
    assert track_score(big_short) > track_score(small_long)


def test_cap_and_merge_caps_to_top_n():
    tracks = [
        Track([_det(0, (0, 0, 10, 10)), _det(30, (0, 0, 10, 10))], "face"),
        Track([_det(0, (200, 200, 260, 260)), _det(30, (200, 200, 260, 260))], "face"),  # biggest
        Track([_det(0, (400, 0, 420, 20)), _det(30, (400, 0, 420, 20))], "face"),
    ]
    out = cap_and_merge_tracks(tracks, max_tracks=2)
    assert len(out) == 2
    # the largest-area track survives the cap
    assert union_bbox(out[0].boxes) == (200, 200, 260, 260)


def test_cap_and_merge_merges_coincident_overlapping_tracks():
    a = Track([_det(0, (10, 10, 50, 50)), _det(12, (10, 10, 50, 50))], "face")
    b = Track([_det(0, (12, 12, 48, 48)), _det(12, (12, 12, 48, 48))], "face")  # nested in a
    out = cap_and_merge_tracks([a, b], max_tracks=4, merge_overlap=0.6)
    assert len(out) == 1
    assert len(out[0].detections) == 4  # both tracks' detections combined, frame-sorted


def test_cap_and_merge_keeps_different_kinds_apart():
    face = Track([_det(0, (10, 10, 50, 50)), _det(12, (10, 10, 50, 50))], "face")
    hand = Track([_det(0, (12, 12, 48, 48)), _det(12, (12, 12, 48, 48))], "hand")
    out = cap_and_merge_tracks([face, hand], max_tracks=4)
    assert len(out) == 2  # a hand overlapping a face is not the same subject
