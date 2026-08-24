"""Read a whole video into an in-memory frame sequence + its native fps.

Complements the two other media helpers in this package:
  * ``frame_extract.extract_frame`` -- ONE frame at an exact index.
  * ``video_frame_extractor`` (pipe) -- frames sampled at an *interval* fps.

Neither returns *every* frame together with the source frame rate, which is what
a frame-for-frame video transform (e.g. SeedVR2 upscale) needs: every source
frame is upscaled and the clip is re-encoded at the original fps. This reader
fills that gap with the same OpenCV idioms the extractor already uses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple, Union

from PIL import Image

logger = logging.getLogger(__name__)

DEFAULT_FPS = 24.0


def read_video_frames(video_path: Union[str, Path]) -> Tuple[List[Image.Image], float]:
    """Read all frames of ``video_path`` as RGB PIL Images plus the native fps.

    Returns ``(frames, fps)``. ``fps`` falls back to :data:`DEFAULT_FPS` when the
    container reports a non-positive rate. Raises ``ValueError`` if the file can't
    be opened or yields no frames.
    """
    import cv2

    path = str(video_path)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"could not open video: {path}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        fps = float(fps) if fps and fps > 0 else DEFAULT_FPS

        frames: List[Image.Image] = []
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            frames.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    finally:
        cap.release()

    if not frames:
        raise ValueError(f"no frames decoded from video: {path}")

    logger.info("[VIDEO_READ] %s: %d frame(s) @ %.3f fps", Path(path).name, len(frames), fps)
    return frames, fps
