"""Replayable stream of SeedVR2-resized source frames.

``video_read.read_video_frames`` decodes a whole clip into a list, which the
streamed video path (see :mod:`stream`) cannot use: the point is never to hold
the source clip in memory at all. This reader yields ONE resized frame at a
time and can be re-opened from frame 0 as often as needed.

Replay is what makes the video path's shrink-on-OOM ladder safe. An OOM
retry re-runs the whole clip at a smaller temporal batch, so the source has to
be producible a second time; re-decoding from the file is deterministic (the
same container, the same decoder, the same area-resize), so a retry sees exactly
the frames the failed attempt saw, and the retry writes into a fresh output
rather than appending to a partial one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

import numpy as np
from PIL import Image

from src.pipelines.pipes._shared.media.video_read import DEFAULT_FPS
from src.pipelines.pipes.generator.seedvr2.resize import CROP_MULTIPLE, prepare_input


class ResizedFrameSource:
    """A video file as a re-iterable stream of area-resized uint8 frames.

    ``fps``, ``frame_count_hint`` and ``frame_shape`` are populated by
    :meth:`probe`, which decodes exactly one frame; ``frame_count_hint`` is the
    container's own frame count and can be wrong or absent (``0``), so it is
    only ever used for a progress estimate, never for geometry.
    """

    def __init__(
        self,
        video_path: Union[str, Path],
        *,
        scale: float,
        target_short_side: int,
        crop_multiple: int = CROP_MULTIPLE,
    ):
        self.video_path = str(video_path)
        self._scale = float(scale)
        self._target_short_side = int(target_short_side)
        self._crop_multiple = int(crop_multiple)
        self.fps: float = DEFAULT_FPS
        self.frame_count_hint: int = 0
        self.frame_shape: Optional[Tuple[int, int]] = None

    def probe(self) -> None:
        """Read the native fps, the container's frame-count hint and the resized
        geometry of the first frame. Raises ``ValueError`` when the file cannot
        be opened or decodes no frames -- the same contract, and the same
        messages, as ``read_video_frames``.
        """
        import cv2

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise ValueError(f"could not open video: {self.video_path}")
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            self.fps = float(fps) if fps and fps > 0 else DEFAULT_FPS
            count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            self.frame_count_hint = int(count) if count and count > 0 else 0

            ret, frame = cap.read()
            if not ret or frame is None:
                raise ValueError(f"no frames decoded from video: {self.video_path}")
            resized = self._resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            cap.release()
        self.frame_shape = (int(resized.shape[0]), int(resized.shape[1]))

    def iter_frames(self) -> Iterator[np.ndarray]:
        """Yield every frame, resized, from the start of the file.

        Each call opens its own capture and releases it when the iterator is
        exhausted, closed or garbage-collected, so an abandoned attempt never
        leaves a decoder open.
        """
        import cv2

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise ValueError(f"could not open video: {self.video_path}")
        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                yield self._resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            cap.release()

    def _resize(self, rgb: np.ndarray) -> np.ndarray:
        prepared = prepare_input(
            Image.fromarray(rgb), self._scale, self._target_short_side, self._crop_multiple,
        )
        return np.asarray(prepared, dtype=np.uint8)
