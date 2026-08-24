"""Pure stabilized-tube-window geometry for the LTX video detailer.

A track (``tracking.py``) is a run of per-frame detections that jitter frame to
frame. Cropping each detection box directly would feed the refiner a window that
shakes -- and window jitter becomes FAKE MOTION inside the tube, which the model
then "refines" into a smeared, unstable face. This module turns a track into a
STABILIZED tube window: one crop rectangle (or a smoothly-moving one) of a
single fixed SIZE, used for every frame of the tube, so the only motion the
refiner sees is the subject's real motion.

Policy (agreed design):
  * Prefer ONE FIXED window for the whole track: the union of the track's boxes,
    padded ~1.8x for context, clamped to the frame.
  * Only when that fixed window would swallow more than ~40% of the frame area
    (a subject that roams across most of the frame) fall back to a SLOWLY-MOVING
    window: a smaller fixed SIZE whose CENTER follows an EMA-smoothed path of the
    detection centers. Size stays constant (never resized per frame) so the tube
    has no scale-pumping artefact; only the center translates, gently.

Temporal extent is left as the track's own ``[start_frame, end_frame]`` here --
snapping the frame COUNT to the causal VAE's ``1 + 8k`` lattice is done at encode
time by padding/trimming the cropped tube (mirroring ``latent_upscaler/ltx``'s
``_pad_frames_to_temporal_grid``), not by moving the window, so no source frame
is ever dropped from the composite.

numpy-only, pure, deterministic -- the whole "does the window shake / snap / clamp
correctly" question is unit-testable without a frame or a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

from src.pipelines.pipes.detailer.video_ltx.tracking import Box, Track, box_area, union_bbox

IntBox = Tuple[int, int, int, int]


@dataclass
class TubeWindow:
    """The stabilized crop rectangle(s) for one track's tube.

    ``boxes`` has exactly one integer ``(x0, y0, x1, y1)`` box per pixel frame in
    ``[start_frame, end_frame]`` (inclusive), and EVERY box is exactly
    ``width x height`` -- a uniform crop size is what lets the cropped frames
    stack into a clean video for the refiner. For a fixed window every box is
    identical; for a moving window they share size and translate."""

    kind: str
    start_frame: int
    end_frame: int
    width: int
    height: int
    boxes: List[IntBox]
    moving: bool

    def box_at(self, frame: int) -> IntBox:
        return self.boxes[frame - self.start_frame]

    @property
    def frames(self) -> range:
        return range(self.start_frame, self.end_frame + 1)


# -- primitive box ops ----------------------------------------------------


def bbox_center(box: Box) -> Tuple[float, float]:
    return ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)


def bbox_size(box: Box) -> Tuple[float, float]:
    return (box[2] - box[0], box[3] - box[1])


def pad_bbox(box: Box, factor: float) -> Box:
    """Scale a box's width and height by ``factor`` about its center."""
    cx, cy = bbox_center(box)
    w, h = bbox_size(box)
    hw, hh = w * factor * 0.5, h * factor * 0.5
    return (cx - hw, cy - hh, cx + hw, cy + hh)


def clamp_bbox(box: Box, frame_w: int, frame_h: int) -> Box:
    """Clip a box to the frame, SHRINKING it where it overhangs."""
    return (
        max(0.0, box[0]), max(0.0, box[1]),
        min(float(frame_w), box[2]), min(float(frame_h), box[3]),
    )


def _place_fixed_size(cx: float, cy: float, w: int, h: int, frame_w: int, frame_h: int) -> IntBox:
    """A ``w x h`` box centered on ``(cx, cy)`` but TRANSLATED wholly inside the
    frame -- size is preserved (never clipped), which is what keeps a moving
    window's crop dimensions constant frame to frame. ``w``/``h`` are assumed
    already <= the frame dims (the caller clamps the size first)."""
    x0 = int(round(cx - w / 2))
    y0 = int(round(cy - h / 2))
    x0 = max(0, min(x0, frame_w - w))
    y0 = max(0, min(y0, frame_h - h))
    return (x0, y0, x0 + w, y0 + h)


def snap_working_resolution(
    w: int, h: int, *, short_side: int = 512, max_short_side: int = 1024,
    downscale: int = 32, min_side: int = 64,
) -> Tuple[int, int]:
    """Working (encode) resolution for a ``w x h`` crop, chosen so the refine
    NEVER destroys resolution before trying to add it.

    The refine's job is to ADD detail; feeding it a window smaller than the crop
    already carries means the very first step throws real pixels away and then
    tries to hallucinate them back. So the working size is picked per crop by its
    SHORT side ``s`` (uniform scale, aspect kept):

      * ``s < short_side``               -- UPSCALE so the short side reaches
        ``short_side``; a small face (e.g. 90px) gets real resolution to refine
        into (the zoom win, unchanged from before).
      * ``short_side <= s <= max_short_side`` -- refine at NATIVE size (scale 1):
        a large crop already carries its own detail; the old unconditional
        downscale-to-512 threw that away.
      * ``s > max_short_side``           -- the ONLY regime that scales DOWN, and
        only to the VRAM-safe cap ``max_short_side`` (a mild, bounded downscale
        of a crop too big to encode whole).

    Both dimensions are snapped UP to the causal VAE's ``downscale`` (32px) grid,
    so at or below the cap the working size is always >= the crop's own size --
    the "never below native" invariant. Only an above-cap crop ends up smaller
    than native, by construction."""
    w, h = int(w), int(h)
    short = max(1, min(w, h))
    if short < short_side:
        scale = short_side / short          # small crop -> upscale to the floor
    elif short > max_short_side:
        scale = max_short_side / short      # oversized crop -> mild cap-downscale
    else:
        scale = 1.0                         # already-large crop -> native refine

    def snap_up(x: float) -> int:
        # ceil, not round: keeps every working dim >= its (scaled) crop dim, so
        # the snap can never nudge a native/upscaled crop back below its source.
        return max(min_side, int(math.ceil(x / downscale)) * downscale)

    return snap_up(w * scale), snap_up(h * scale)


# -- center smoothing (moving window) ------------------------------------


def ema_smooth(values: Sequence[float], alpha: float) -> List[float]:
    """Causal exponential moving average: ``s[i] = a*v[i] + (1-a)*s[i-1]``."""
    out: List[float] = []
    prev = None
    for v in values:
        prev = float(v) if prev is None else alpha * float(v) + (1.0 - alpha) * prev
        out.append(prev)
    return out


def interpolate_centers(
    sample_frames: Sequence[int], centers: Sequence[Tuple[float, float]], frames: Sequence[int],
) -> List[Tuple[float, float]]:
    """Linearly interpolate ``(cx, cy)`` from the sparse sampled frames to every
    frame in ``frames`` (holding the endpoints flat outside the sampled range)."""
    sf = np.asarray(sample_frames, dtype=np.float64)
    cx = np.asarray([c[0] for c in centers], dtype=np.float64)
    cy = np.asarray([c[1] for c in centers], dtype=np.float64)
    fq = np.asarray(list(frames), dtype=np.float64)
    ix = np.interp(fq, sf, cx)
    iy = np.interp(fq, sf, cy)
    return [(float(ix[i]), float(iy[i])) for i in range(len(fq))]


# -- the entry point ------------------------------------------------------


def stabilize_window(
    track: Track,
    frame_w: int,
    frame_h: int,
    *,
    pad_factor: float = 1.8,
    area_threshold: float = 0.40,
    ema_alpha: float = 0.1,
) -> TubeWindow:
    """Turn a track into a stabilized :class:`TubeWindow` (see module docstring).

    ``area_threshold`` is the fraction of frame area above which the padded
    union window is judged "too big to hold still" and the moving fallback is
    used. ``ema_alpha`` (small = heavier smoothing) governs how sluggishly the
    moving window's center chases the subject -- deliberately low so the window
    lags real motion slightly rather than transmitting per-frame detection
    jitter into the tube."""
    start, end = track.start_frame, track.end_frame
    frame_area = float(frame_w * frame_h)

    padded_union = clamp_bbox(pad_bbox(union_bbox(track.boxes), pad_factor), frame_w, frame_h)

    if box_area(padded_union) <= area_threshold * frame_area:
        x0, y0, x1, y1 = padded_union
        ibox: IntBox = (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
        w, h = ibox[2] - ibox[0], ibox[3] - ibox[1]
        boxes = [ibox for _ in range(start, end + 1)]
        return TubeWindow(track.kind, start, end, w, h, boxes, moving=False)

    # Moving fallback: a fixed SIZE (the largest single padded detection, capped
    # to the frame) whose center follows the EMA-smoothed detection path.
    sizes = [bbox_size(pad_bbox(d.box, pad_factor)) for d in track.detections]
    win_w = min(frame_w, int(round(max(s[0] for s in sizes))))
    win_h = min(frame_h, int(round(max(s[1] for s in sizes))))

    sample_frames = [d.frame for d in track.detections]
    raw_centers = [bbox_center(d.box) for d in track.detections]
    sm_cx = ema_smooth([c[0] for c in raw_centers], ema_alpha)
    sm_cy = ema_smooth([c[1] for c in raw_centers], ema_alpha)
    per_frame = interpolate_centers(
        sample_frames, list(zip(sm_cx, sm_cy)), range(start, end + 1))

    boxes = [_place_fixed_size(cx, cy, win_w, win_h, frame_w, frame_h) for cx, cy in per_frame]
    return TubeWindow(track.kind, start, end, win_w, win_h, boxes, moving=True)
