"""Stitch a Wan chain-video's per-segment mp4 files into one continuous video.

Each non-first segment joins the previous one over its own `overlaps[i - 1]`
-- the count is PER JOIN, not one blanket value for the whole chain: a fresh
t2v/i2v/flf cut shares no conditioning with what came before it, so its join
is 0 regardless of what any other segment in the chain did; a `chain`
continuation only carries a duplicated leading window when its generator
didn't already trim it (see chain_video_wan22/main.py's per-segment
`context_trimmed` bookkeeping) -- that decision is per segment, so one
segment's trim state must never change another join's overlap. Each
non-zero join's leading `overlap` frames duplicate the previous segment's
tail (they were fed back in as that segment's i2v/continuation start
conditioning), so the stitched output crossfades them down instead of
keeping a duplicate. Reads each segment's frames sequentially -- one
segment's `cv2.VideoCapture` open at a time, never all segments concurrently
-- so peak memory during the read phase is one segment's worth of frames,
not the whole chain's; the accumulated (post-drop) frame list is still
handed to `encode_frames_to_mp4` as one buffer (the shared encode helper
writes a single ffmpeg stdin buffer, so a byte-for-byte streaming write
isn't available without bypassing it -- not worth the duplication for chain
videos, which are bounded in length).

That cost argument doesn't apply when every join's overlap is 0: there's
nothing to blend, so nothing requires decoding frames back out of the
segment files at all. Every segment comes from the same `encode_frames_to_mp4`
call at one fixed (width, height) (see chain_video_wan22/main.py), so codec
and resolution can't differ segment-to-segment; frame rate is checked
cheaply via ffprobe. When that holds and `ffmpeg` is on PATH, the
all-joins-zero case runs through ffmpeg's concat demuxer in `-c copy`
(stream-copy) mode instead -- no decode, no re-encode, no quality loss. Any
precondition failure (no ffmpeg, an unprobeable or mismatched-fps segment),
any single join being non-zero, or an ffmpeg error falls back to the
frame-accurate path below unchanged.

Both the frame reader and the encode call are injectable so this can be unit
tested without cv2 or ffmpeg.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
FrameReader = Callable[[PathLike], Iterable[np.ndarray]]
Encoder = Callable[[np.ndarray, PathLike, float], object]


def _default_frame_reader(path: PathLike) -> Iterator[np.ndarray]:
    """Yield RGB uint8 (H, W, 3) frames from a video file via cv2, one at a time."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"could not open segment video: {path}")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def _crossfade_overlap(prev_tail: List[np.ndarray], curr_head: List[np.ndarray]) -> List[np.ndarray]:
    """Linear crossfade over `overlap` frames: frame k weights the previous
    segment's tail down and the current segment's head up as k advances, so the
    two segments dissolve across the seam instead of one being dropped."""
    overlap = len(prev_tail)
    blended: List[np.ndarray] = []
    for k in range(overlap):
        alpha = (k + 1) / (overlap + 1)
        mix = (1.0 - alpha) * prev_tail[k].astype(np.float32) + alpha * curr_head[k].astype(np.float32)
        blended.append(np.clip(np.rint(mix), 0, 255).astype(np.uint8))
    return blended


def _can_stream_copy(segment_paths: List[PathLike], fps: float) -> bool:
    """Whether `segment_paths` are safe to concatenate via ffmpeg's concat
    demuxer in `-c copy` mode: `ffmpeg` must be on PATH, and every segment's
    real frame rate (probed via ffprobe) must match the target `fps` -- an
    unprobeable segment (missing ffprobe, corrupt file) or a mismatch is
    treated as "can't tell it's safe", not "assume it's fine".
    """
    if shutil.which("ffmpeg") is None:
        return False

    from src.pipelines.pipes._shared.media.video_encode import probe_source_fps

    for path in segment_paths:
        actual = probe_source_fps(path)
        if actual is None or abs(actual - fps) > 0.05:
            return False
    return True


def _stream_copy_concat(segment_paths: List[PathLike], out_path: PathLike) -> bool:
    """Concatenate `segment_paths` into `out_path` via ffmpeg's concat demuxer
    with `-c copy` -- no decode, no re-encode. Returns True on success, False
    on any ffmpeg failure (never raises); the caller falls back to the
    frame-accurate path on False.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        list_path = Path(tmp_dir) / "concat_list.txt"
        lines = [
            "file '{}'".format(str(Path(p).resolve()).replace("'", "'\\''"))
            for p in segment_paths
        ]
        list_path.write_text("\n".join(lines) + "\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy",
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
        except subprocess.TimeoutExpired:
            return False

    return result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def stitch_segments(
    segment_paths: List[PathLike],
    overlaps: Sequence[int],
    out_path: PathLike,
    fps: float,
    *,
    frame_reader: FrameReader = _default_frame_reader,
    encode: Optional[Encoder] = None,
) -> PathLike:
    """Concatenate `segment_paths` into `out_path`, crossfading each non-first
    segment's leading `overlaps[i - 1]` frames with the previous segment's
    tail of the same length (they cover the same instant -- the segment was
    conditioned on that tail), so the seam dissolves instead of a hard cut.

    `overlaps` has exactly `len(segment_paths) - 1` entries, one per join,
    each >= 0 (0 => that join is a plain concatenation, no blend); segment 0
    is never trimmed and has no corresponding entry. The caller (
    chain_video_wan22/main.py) derives each join's value from that segment's
    OWN executed geometry -- one segment's overlap must never leak onto
    another join. Raises `ValueError` if `segment_paths` is empty, if
    `overlaps` isn't exactly `len(segment_paths) - 1` long, if any non-first
    segment has fewer frames than its own join's overlap (nothing would be
    left to include from it after the blended region), or if a join's
    overlap exceeds the frames accumulated so far (a caller bug -- silently
    clipping here would discard footage instead of crossfading it).

    When every join's overlap is 0, this first tries an ffmpeg concat-demuxer
    stream-copy (see `_can_stream_copy`/`_stream_copy_concat`) -- no decode,
    no re-encode. Any precondition failure, any non-zero join, or an ffmpeg
    error falls through to the frame-accurate path unchanged.
    """
    if not segment_paths:
        raise ValueError("stitch_segments requires at least one segment path")
    overlaps = [max(0, int(o)) for o in overlaps]
    if len(overlaps) != len(segment_paths) - 1:
        raise ValueError(
            f"stitch_segments: expected {len(segment_paths) - 1} overlap value(s) "
            f"for {len(segment_paths)} segment(s), got {len(overlaps)}"
        )

    if all(o == 0 for o in overlaps) and _can_stream_copy(segment_paths, fps):
        if _stream_copy_concat(segment_paths, out_path):
            logger.info(
                "[STITCH] %d segment(s) -> %s via ffmpeg stream-copy concat "
                "(all joins zero overlap, no decode/re-encode)",
                len(segment_paths), out_path,
            )
            return out_path

    if encode is None:
        from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4 as encode

    frames: List[np.ndarray] = []
    for i, path in enumerate(segment_paths):
        segment = list(frame_reader(path))
        overlap = 0 if i == 0 else overlaps[i - 1]
        if overlap == 0:
            frames.extend(segment)
            continue
        if len(segment) <= overlap:
            raise ValueError(
                f"stitch_segments: segment {i} ({path}) has only {len(segment)} frame(s), "
                f"not enough to drop its {overlap}-frame join overlap"
            )
        if len(frames) < overlap:
            # A slice-assign of frames[-overlap:] would silently clip to
            # whatever's accumulated instead of raising -- crossfading fewer
            # frames than the caller asked for and discarding the rest of
            # the incoming segment's replayed context as if it were real
            # content. The caller's overlap must never exceed what's
            # actually there to blend against.
            raise ValueError(
                f"stitch_segments: join {i} overlap ({overlap}) exceeds the {len(frames)} "
                f"frame(s) accumulated so far -- would silently discard footage instead "
                f"of crossfading it"
            )
        frames[-overlap:] = _crossfade_overlap(frames[-overlap:], segment[:overlap])
        frames.extend(segment[overlap:])

    if not frames:
        raise ValueError("stitch_segments: no frames remained after dropping overlaps")

    encode(np.stack(frames, axis=0), out_path, fps)
    logger.info(
        "[STITCH] %d segment(s) -> %s via frame-accurate decode/re-encode (overlaps=%s)",
        len(segment_paths), out_path, overlaps,
    )
    return out_path
