from __future__ import annotations

from typing import Callable, Iterable, List, Optional

import cv2
import numpy as np

from src.platform.runtime.pose.layout import NUM_POINTS
from src.platform.runtime.pose.temporal import fill_gaps, hold_lost, lost_frames, smooth
from src.platform.runtime.pose.tracking import PersonTracker, PoseBackend


class NoPersonError(ValueError):
    pass


def shrink_to_short_side(rgb: np.ndarray, short_side: int) -> np.ndarray:
    height, width = rgb.shape[:2]
    ratio = short_side / float(min(height, width))
    if abs(ratio - 1.0) < 1e-6:
        return rgb
    size = (max(1, int(round(width * ratio))), max(1, int(round(height * ratio))))
    interpolation = cv2.INTER_AREA if ratio < 1.0 else cv2.INTER_LANCZOS4
    return cv2.resize(rgb, size, interpolation=interpolation)


def estimate_sequence(
    frames: Iterable[np.ndarray],
    backend: PoseBackend,
    keyframe_interval: int = 8,
    min_confidence: float = 0.3,
    detect_resolution: int = 512,
    on_frame: Optional[Callable[[int], None]] = None,
) -> np.ndarray:
    tracker = PersonTracker(backend, keyframe_interval, min_confidence)
    poses: List[np.ndarray] = []
    for index, frame in enumerate(frames):
        small = shrink_to_short_side(frame, detect_resolution)
        pose = tracker.process(small, index)
        pose = pose.copy()
        pose[:, 0] *= frame.shape[1] / small.shape[1]
        pose[:, 1] *= frame.shape[0] / small.shape[0]
        poses.append(pose)
        if on_frame is not None:
            on_frame(index)
    if not poses:
        raise NoPersonError("the video has no frames")
    return np.stack(poses).astype(np.float32)


def clean_sequence(
    raw: np.ndarray, fps: float, min_confidence: float = 0.3, max_gap: int = 12, smoothing: float = 0.5,
) -> np.ndarray:
    if raw.ndim != 3 or raw.shape[1:] != (NUM_POINTS, 3):
        raise ValueError(f"expected a (T, {NUM_POINTS}, 3) pose sequence, got {raw.shape}")
    if lost_frames(raw, min_confidence).all():
        raise NoPersonError("no person found in the video")
    filled = fill_gaps(raw, min_confidence, max_gap)
    held = hold_lost(filled, min_confidence)
    return smooth(held, fps, smoothing, min_confidence)
