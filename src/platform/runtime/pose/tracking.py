from __future__ import annotations

from typing import Optional, Protocol, Sequence

import numpy as np

from src.platform.runtime.pose.layout import BODY, empty_pose

MIN_BODY_POINTS = 4
BOX_PADDING = 0.15
BOX_MIN_PIXELS = 12.0
IOU_ACCEPT = 0.15


class PoseBackend(Protocol):
    def detect(self, rgb: np.ndarray) -> np.ndarray: ...

    def estimate(self, rgb: np.ndarray, box: Sequence[float]) -> np.ndarray: ...


def box_iou(a: Sequence[float], b: Sequence[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def body_count(pose: np.ndarray, min_confidence: float) -> int:
    return int((pose[BODY, 2] >= min_confidence).sum())


def pose_box(pose: np.ndarray, min_confidence: float, width: int, height: int) -> Optional[np.ndarray]:
    valid = pose[:, 2] >= min_confidence
    if not valid.any():
        return None
    points = pose[valid, :2]
    lo, hi = points.min(axis=0), points.max(axis=0)
    pad = np.maximum((hi - lo) * BOX_PADDING, BOX_MIN_PIXELS)
    box = np.array([lo[0] - pad[0], lo[1] - pad[1], hi[0] + pad[0], hi[1] + pad[1]], dtype=np.float32)
    box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
    box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
    return box


class PersonTracker:
    def __init__(self, backend: PoseBackend, keyframe_interval: int = 8, min_confidence: float = 0.3):
        self.backend = backend
        self.keyframe_interval = max(1, int(keyframe_interval))
        self.min_confidence = float(min_confidence)
        self.box: Optional[np.ndarray] = None
        self.detections = 0

    def _choose(self, detections: np.ndarray) -> Optional[np.ndarray]:
        if len(detections) == 0:
            return None
        if self.box is not None:
            overlaps = np.array([box_iou(self.box, det[:4]) for det in detections])
            best = int(overlaps.argmax())
            if overlaps[best] >= IOU_ACCEPT:
                return detections[best, :4]
        areas = (detections[:, 2] - detections[:, 0]) * (detections[:, 3] - detections[:, 1])
        return detections[int((areas * detections[:, 4]).argmax()), :4]

    def process(self, rgb: np.ndarray, index: int) -> np.ndarray:
        height, width = rgb.shape[:2]
        box = self.box
        if box is None or index % self.keyframe_interval == 0:
            self.detections += 1
            chosen = self._choose(self.backend.detect(rgb))
            if chosen is not None:
                box = chosen
        if box is None:
            return empty_pose()
        pose = self.backend.estimate(rgb, box)
        if body_count(pose, self.min_confidence) < MIN_BODY_POINTS:
            self.box = None
            return empty_pose()
        self.box = pose_box(pose, self.min_confidence, width, height)
        return pose.astype(np.float32)
