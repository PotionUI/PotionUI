"""Pure detection-linking + scene-cut math for the LTX video detailer.

The video detailer refines faces/hands as spatiotemporal TUBES: a detection in
one frame is only useful once linked, across time, into a *track* -- the same
face followed through the clip. This module owns that linking and everything
downstream of it that is pure geometry/statistics:

  detections per sampled frame  --link_detections-->  raw tracks
       --split_tracks_at_cuts-->  --filter_short_tracks-->  --cap_and_merge_tracks-->  final tracks

Deliberately dependency-light: numpy only (for the histogram scene-cut score),
no torch, no cv2, no model. Every function is deterministic and side-effect
free so the tracking behaviour -- the part most likely to misbehave on real
footage (crossing faces, a single giant face, a hard cut, no detections at
all) -- can be tested hard on CPU without a detector or a video. The impure
half (running the shared MediaPipe/YOLO detectors over sampled frames) lives in
``detection.py`` and only feeds this module the boxes it produces.

Box convention throughout: ``(x1, y1, x2, y2)`` in pixel coordinates, matching
the shared ``BaseDetector.detect`` output (``_shared/detection/base_detector.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

import numpy as np

Box = Tuple[float, float, float, float]


@dataclass
class Detection:
    """One detection at one (sampled) pixel-frame index."""

    frame: int
    box: Box
    kind: str  # "face" | "hand"


@dataclass
class Track:
    """A time-ordered run of detections of the SAME kind -- one subject followed
    across sampled frames. ``detections`` is sorted by ``frame`` ascending."""

    detections: List[Detection]
    kind: str
    # Filled in by ``detection.py`` for logging/telemetry only -- never read by
    # the pure math here.
    meta: dict = field(default_factory=dict)

    @property
    def start_frame(self) -> int:
        return self.detections[0].frame

    @property
    def end_frame(self) -> int:
        return self.detections[-1].frame

    @property
    def boxes(self) -> List[Box]:
        return [d.box for d in self.detections]


# -- geometry -------------------------------------------------------------


def iou(a: Box, b: Box) -> float:
    """Intersection-over-union of two ``(x1, y1, x2, y2)`` boxes (0.0 when
    disjoint or either box is degenerate)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def box_area(b: Box) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def union_bbox(boxes: Sequence[Box]) -> Box:
    """Smallest box covering every input box."""
    xs1 = min(b[0] for b in boxes)
    ys1 = min(b[1] for b in boxes)
    xs2 = max(b[2] for b in boxes)
    ys2 = max(b[3] for b in boxes)
    return (xs1, ys1, xs2, ys2)


def overlap_fraction(a: Box, b: Box) -> float:
    """Intersection area over the SMALLER box's area (in [0, 1]).

    Unlike :func:`iou`, this is ~1.0 when the smaller box is nearly contained
    in the larger even if the larger dwarfs it -- the right test for "are these
    two tracks looking at the same region" (a small detection window that sits
    entirely inside a big one is the same subject, IoU would read low)."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    smaller = min(box_area(a), box_area(b))
    return inter / smaller if smaller > 0.0 else 0.0


# -- track scoring --------------------------------------------------------


def track_span_frames(track: Track) -> int:
    """Temporal extent in pixel frames (inclusive)."""
    return track.end_frame - track.start_frame + 1


def track_score(track: Track) -> float:
    """Rank score for the top-N cap: union-window AREA x temporal DURATION.

    A big face on screen a long time outranks a tiny one that flickers -- both
    axes matter (a huge face for two frames, or a speck for the whole clip, are
    both lower priority than a solid mid-size subject present throughout)."""
    return box_area(union_bbox(track.boxes)) * float(track_span_frames(track))


# -- linking --------------------------------------------------------------


def link_detections(
    frame_detections: Sequence[Tuple[int, Sequence[Detection]]],
    *,
    iou_threshold: float = 0.3,
    max_gap_steps: int = 1,
) -> List[Track]:
    """Greedy IoU linking of per-sampled-frame detections into tracks.

    ``frame_detections`` is an ORDERED list of ``(frame_index, [Detection, ...])``
    -- one entry per SAMPLED frame (the detection stride), earliest first, all
    of ONE kind (link faces to faces, hands to hands: the caller groups by
    kind). Each new frame's detections are matched to the still-active tracks by
    best IoU against each track's most recent box; a match above
    ``iou_threshold`` extends that track, an unmatched detection starts a new
    one. A track survives up to ``max_gap_steps`` consecutive sampled frames
    with no match (a brief occlusion / a missed detection at one stride) before
    it is closed -- without that tolerance a single dropped frame would shatter
    one subject into two tracks.

    Greedy-by-descending-IoU (not Hungarian): O(d*a) per frame, deterministic,
    and more than good enough at a 6-frame stride where subjects barely move
    between samples -- an exact assignment buys nothing here and adds a
    dependency.
    """
    active: List[dict] = []  # {"track": Track, "last_step": int}
    done: List[Track] = []

    for step, (_frame, dets) in enumerate(frame_detections):
        # Close tracks whose run of MISSED steps (steps between the last match
        # and now, exclusive) already exceeds the tolerance -- steps only
        # increase, so such a track can never be extended again. ``max_gap_steps
        # == 1`` therefore permits exactly one missed sampled frame between
        # matches (last match at step L extends at step L+2).
        survivors = []
        for a in active:
            if step - a["last_step"] - 1 > max_gap_steps:
                done.append(a["track"])
            else:
                survivors.append(a)
        active = survivors

        # Rank every (detection, active-track) pair by IoU, then assign greedily
        # -- each detection and each track used at most once.
        candidates = []
        for di, d in enumerate(dets):
            for ai, a in enumerate(active):
                score = iou(d.box, a["track"].detections[-1].box)
                if score >= iou_threshold:
                    candidates.append((score, di, ai))
        candidates.sort(key=lambda c: c[0], reverse=True)

        used_det: set = set()
        used_active: set = set()
        for score, di, ai in candidates:
            if di in used_det or ai in used_active:
                continue
            active[ai]["track"].detections.append(dets[di])
            active[ai]["last_step"] = step
            used_det.add(di)
            used_active.add(ai)

        for di, d in enumerate(dets):
            if di not in used_det:
                active.append({"track": Track([d], d.kind), "last_step": step})

    done.extend(a["track"] for a in active)
    # Stable order: earliest-starting track first (deterministic downstream).
    done.sort(key=lambda t: (t.start_frame, t.end_frame))
    return done


# -- scene cuts -----------------------------------------------------------


def frame_histogram(frame: np.ndarray, bins: int = 32) -> np.ndarray:
    """Per-channel intensity histogram of an ``(H, W, 3)`` uint8 frame,
    L1-normalized and concatenated to one ``(3*bins,)`` vector that sums to 1."""
    hist = np.concatenate([
        np.histogram(frame[..., c], bins=bins, range=(0, 255))[0].astype(np.float64)
        for c in range(frame.shape[-1])
    ])
    total = hist.sum()
    return hist / total if total > 0 else hist


def histogram_distance(h1: np.ndarray, h2: np.ndarray) -> float:
    """L1 distance halved -> [0, 1]. 0 = identical distributions, 1 = disjoint.

    (Each histogram sums to 1, so ``sum|h1 - h2|`` is in ``[0, 2]``; halving
    normalizes it to a clean 0..1 "how different is this shot" score.)"""
    return float(np.abs(h1 - h2).sum() * 0.5)


def detect_scene_cuts(
    frames: Sequence[np.ndarray],
    sample_indices: Sequence[int],
    *,
    threshold: float = 0.35,
) -> List[int]:
    """Find hard cuts among the SAMPLED frames via histogram divergence.

    Returns the sorted list of frame indices at which a cut BEGINS -- i.e. a
    sampled frame whose colour distribution jumped by more than ``threshold``
    from the previous sampled frame. Cheap (no motion estimation, just a global
    colour histogram per sampled frame) and intentionally conservative:
    ``threshold`` is high enough that a gradual pan does not register, only a
    genuine shot change, because a false cut needlessly severs a valid track
    while a missed soft transition merely leaves one slightly longer track."""
    cuts: List[int] = []
    prev_hist = None
    for idx in sample_indices:
        hist = frame_histogram(frames[idx])
        if prev_hist is not None and histogram_distance(prev_hist, hist) > threshold:
            cuts.append(int(idx))
        prev_hist = hist
    return cuts


def split_tracks_at_cuts(tracks: Sequence[Track], cut_frames: Sequence[int]) -> List[Track]:
    """Split any track that straddles a scene cut into per-shot sub-tracks.

    A track whose detections span a cut frame is severed there: a subject that
    happens to occupy the same screen position across a shot change is NOT the
    same tube (the pixels behind it are unrelated), and refining across the cut
    would bleed one shot's texture into the other's."""
    cut_set = sorted(set(int(c) for c in cut_frames))
    if not cut_set:
        return list(tracks)

    out: List[Track] = []
    for track in tracks:
        segment: List[Detection] = []
        for det in track.detections:
            # Start a new segment whenever this detection lands on/after a cut
            # that the previous detection was before.
            if segment and any(segment[-1].frame < c <= det.frame for c in cut_set):
                out.append(Track(segment, track.kind))
                segment = []
            segment.append(det)
        if segment:
            out.append(Track(segment, track.kind))
    return out


# -- filtering / capping / merging ---------------------------------------


def filter_short_tracks(tracks: Sequence[Track], min_frames: int) -> List[Track]:
    """Drop tracks whose temporal extent is shorter than ``min_frames`` (a
    fleeting/spurious detection: too brief to refine as a stable tube, and its
    per-frame temporal ramp would be almost all fade-in/out anyway)."""
    return [t for t in tracks if track_span_frames(t) >= min_frames]


def _temporal_overlap(a: Track, b: Track) -> bool:
    return a.start_frame <= b.end_frame and b.start_frame <= a.end_frame


def cap_and_merge_tracks(
    tracks: Sequence[Track],
    *,
    max_tracks: int = 4,
    merge_overlap: float = 0.6,
) -> List[Track]:
    """Merge spatially-coincident tracks, then keep the top ``max_tracks`` by
    :func:`track_score`.

    Two tracks are merged when their union windows overlap (smaller-box
    fraction, :func:`overlap_fraction`) by more than ``merge_overlap`` AND they
    coexist in time -- e.g. a face detector and (a future) second pass both
    firing on one subject, or one subject that briefly split into two track
    fragments. Merging keeps the tube count (and therefore the refine cost)
    down and avoids two overlapping feathered pastes fighting over the same
    pixels. The cap then bounds total work to the ``max_tracks`` most
    significant subjects (largest area x longest duration)."""
    merged: List[Track] = []
    for track in sorted(tracks, key=track_score, reverse=True):
        placed = False
        tb = union_bbox(track.boxes)
        for m in merged:
            if m.kind == track.kind and _temporal_overlap(m, track) and \
                    overlap_fraction(tb, union_bbox(m.boxes)) > merge_overlap:
                m.detections.extend(track.detections)
                m.detections.sort(key=lambda d: d.frame)
                placed = True
                break
        if not placed:
            merged.append(Track(list(track.detections), track.kind))

    merged.sort(key=track_score, reverse=True)
    return merged[: max(0, int(max_tracks))]
