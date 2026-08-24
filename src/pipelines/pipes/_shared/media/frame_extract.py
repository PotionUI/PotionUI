"""Frame-exact video frame extraction, shared by ``video_frame_extractor`` and
any pipe that needs a single specific frame (e.g. the last frame of a clip for
chained/FLF video conditioning) rather than an fps-interval sequence.

Uses the same OpenCV idioms as ``video_frame_extractor`` (``CAP_PROP_FRAME_COUNT``,
``CAP_PROP_POS_FRAMES`` seek + ``read()``), with a sequential-read fallback for
codecs where seeking to an arbitrary frame index is unreliable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from PIL import Image

logger = logging.getLogger(__name__)


def extract_frame(video_path: Union[str, Path], index: int = -1) -> Image.Image:
    """Extract exactly one frame from ``video_path`` as a PIL Image (RGB).

    ``index`` follows Python indexing: ``0`` is the first frame, ``-1`` (default)
    is the last frame. Raises ``ValueError`` if the file can't be opened, has no
    frames, or ``index`` is out of range.
    """
    import cv2

    video_path = str(video_path)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"could not open video: {video_path}")

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        resolved = index if index >= 0 else total_frames + index
        if total_frames <= 0 or resolved < 0 or (total_frames and resolved >= total_frames):
            raise ValueError(
                f"frame index {index} out of range for {video_path} "
                f"({total_frames} frame(s) reported)"
            )

        # Direct seek + read -- fast path, but seek-to-frame is codec-dependent
        # and can silently return the wrong frame (or fail) on some containers.
        cap.set(cv2.CAP_PROP_POS_FRAMES, resolved)
        ret, frame = cap.read()
        if not ret or frame is None:
            frame = _sequential_read(cap, resolved, video_path)

        return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()


def _sequential_read(cap, resolved: int, video_path: str):
    """Fallback for containers where ``CAP_PROP_POS_FRAMES`` seeking fails:
    rewind to the start and read forward, counting frames."""
    logger.warning(
        "[FRAME_EXTRACT] seek-to-frame failed for %s (frame %d); falling back to "
        "sequential read", video_path, resolved,
    )
    import cv2

    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame = None
    for i in range(resolved + 1):
        ret, candidate = cap.read()
        if not ret or candidate is None:
            raise ValueError(
                f"could not read frame {resolved} of {video_path} (stream ended at frame {i})"
            )
        frame = candidate
    if frame is None:
        raise ValueError(f"could not read frame {resolved} of {video_path}")
    return frame
