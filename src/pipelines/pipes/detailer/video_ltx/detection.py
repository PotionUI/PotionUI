"""Per-frame detection seam for the LTX video detailer.

This is the ONLY impure half of the tracking stack: it runs a real face/hand
detector over sampled frames and hands the resulting boxes to ``tracking.py``'s
pure linking/cut/merge math. Kept behind a tiny :class:`FrameDetector` seam
(``detect(pil) -> [box, ...]``) so the whole track-building orchestration
(:func:`detect_tracks`) can be exercised on CPU with stub detectors -- no
mediapipe, no YOLO, no model files, no cv2.

Backends:
  * ``mediapipe`` (DEFAULT, Apache-2.0): MediaPipe FaceLandmarker / HandLandmarker.
    This is the default on purpose -- the Ultralytics YOLO models are AGPL-3.0
    and must never be the out-of-the-box path (licensing constraint). The
    FaceLandmarker call mirrors ``_shared/detection/detailer_helper.py``'s
    ``detect_mediapipe`` (the shared stack has a FACE mediapipe path but its
    ``HandDetector`` mediapipe branch is an unimplemented stub, so the hand
    equivalent is written here the same way).
  * ``yolo`` (OPT-IN, AGPL-3.0): reuses the shared ``FaceDetector``/
    ``HandDetector`` + ``DetailerHelper`` unchanged. Lazy-imported only when
    selected, because that module imports ``ultralytics`` (hence cv2/torch) at
    import time.

All detector construction and heavy imports are lazy -- importing THIS module is
free (numpy + the pure tracking helpers only), so the pipe module and its tests
import cleanly in the cv2-less test container.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Sequence

import numpy as np

from src.pipelines.pipes.detailer.video_ltx.tracking import (
    Box,
    Detection,
    Track,
    cap_and_merge_tracks,
    detect_scene_cuts,
    filter_short_tracks,
    link_detections,
    split_tracks_at_cuts,
)

logger = logging.getLogger(__name__)


# -- detector seam --------------------------------------------------------


class FrameDetector(ABC):
    """Detect one kind of subject in a single frame."""

    kind: str = "face"

    @abstractmethod
    def detect(self, image) -> List[Box]:  # image: PIL.Image.Image
        """Return ``[(x1, y1, x2, y2), ...]`` in pixel coords (may be empty)."""
        raise NotImplementedError


def _landmarks_bbox(landmarks, width: int, height: int) -> Box:
    xs = [lm.x * width for lm in landmarks]
    ys = [lm.y * height for lm in landmarks]
    return (min(xs), min(ys), max(xs), max(ys))


class MediaPipeFaceDetector(FrameDetector):
    """MediaPipe FaceLandmarker -> face bounding boxes (Apache-2.0).

    Mirrors ``DetailerHelper.detect_mediapipe`` (same Tasks API, same
    landmark-extent bbox); reimplemented here only so the video detailer does
    not have to construct the SDXL detailer's whole ``config["detections"]``
    schema just to reach the shared method."""

    kind = "face"

    def __init__(self, model_path: str, confidence: float = 0.5, max_faces: int = 10):
        self.model_path = model_path
        self.confidence = float(confidence)
        self.max_faces = int(max_faces)

    def detect(self, image) -> List[Box]:
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import mediapipe as mp

        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=self.max_faces,
            min_face_detection_confidence=self.confidence,
        )
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(image))
            result = landmarker.detect(mp_image)
            faces = getattr(result, "face_landmarks", None) or []
            return [_landmarks_bbox(f, image.width, image.height) for f in faces]


class MediaPipeHandDetector(FrameDetector):
    """MediaPipe HandLandmarker -> hand bounding boxes (Apache-2.0). Fills the
    gap the shared ``HandDetector``'s mediapipe branch left as a stub."""

    kind = "hand"

    def __init__(self, model_path: str, confidence: float = 0.5, max_hands: int = 6):
        self.model_path = model_path
        self.confidence = float(confidence)
        self.max_hands = int(max_hands)

    def detect(self, image) -> List[Box]:
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import mediapipe as mp

        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=self.model_path),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=self.max_hands,
            min_hand_detection_confidence=self.confidence,
        )
        with vision.HandLandmarker.create_from_options(options) as landmarker:
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.array(image))
            result = landmarker.detect(mp_image)
            hands = getattr(result, "hand_landmarks", None) or []
            return [_landmarks_bbox(h, image.width, image.height) for h in hands]


class _SharedYoloDetector(FrameDetector):
    """Adapter over the shared ``FaceDetector``/``HandDetector`` YOLO path
    (AGPL, opt-in). Lazy-built: the shared module imports ultralytics."""

    def __init__(self, kind: str, model_path: str, confidence: float):
        self.kind = kind
        self._model_path = model_path
        self._confidence = float(confidence)
        self._impl = None

    def _build(self):
        from src.pipelines.pipes._shared.detection import FaceDetector, HandDetector
        from src.pipelines.pipes._shared.detection.detailer_helper import DetailerHelper

        cfg = {
            "device": "cuda",
            "detections": {self.kind: {
                "type": "yolo", "model": self._model_path, "confidence": self._confidence,
                "device": "cuda", "padding": 0, "box_color": [0, 0, 0], "box_thickness": 8,
                "mask_min_ratio": 0.0, "mask_max_ratio": 1.0, "mask_blur": 0,
            }},
        }
        helper = DetailerHelper(cfg)
        det_cls = FaceDetector if self.kind == "face" else HandDetector
        return det_cls(cfg["detections"][self.kind], helper)

    def detect(self, image) -> List[Box]:
        if self._impl is None:
            self._impl = self._build()
        return [tuple(float(v) for v in box) for box in self._impl.detect(image)]


@dataclass
class DetectorBuildResult:
    """Detectors actually runnable, plus which requested kind(s) got skipped.

    Detection models are downloaded on demand (never bundled), so a kind's model
    file may simply not exist yet on a fresh install. That must never fail the
    whole generation, and a missing hand model must never block face detection
    (or vice-versa) -- each kind degrades independently. ``missing`` holds the
    ``kind`` string ("face"/"hand") for every requested-but-unavailable model,
    so the caller can tell the user exactly what to expect."""

    detectors: List[FrameDetector] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


def _model_file_available(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def build_frame_detectors(
    *,
    detect_faces: bool,
    detect_hands: bool,
    backend: str = "mediapipe",
    face_model: str = "models/mediapipe/face_landmarker.task",
    hand_model: str = "models/mediapipe/hand_landmarker.task",
    face_yolo_model: str = "models/detection_bbox/face_yolov12m.pt",
    hand_yolo_model: str = "models/detection_bbox/hand_yolov8n.pt",
    confidence: float = 0.5,
) -> DetectorBuildResult:
    """Construct the enabled detectors for ``backend``. MediaPipe (Apache-2.0)
    is the default; ``yolo`` (AGPL) is opt-in only.

    A requested kind whose backing model file is not on disk is SKIPPED rather
    than constructed -- ``mediapipe``/``yolo`` would otherwise only fail once
    ``.detect()`` actually runs, deep inside the per-frame loop, with a raw
    library error naming an internal path. Checking existence up front lets the
    pipe degrade gracefully and tell the user which model to download."""
    detectors: List[FrameDetector] = []
    missing: List[str] = []
    if backend == "yolo":
        if detect_faces:
            if _model_file_available(face_yolo_model):
                detectors.append(_SharedYoloDetector("face", face_yolo_model, confidence))
            else:
                missing.append("face")
        if detect_hands:
            if _model_file_available(hand_yolo_model):
                detectors.append(_SharedYoloDetector("hand", hand_yolo_model, confidence))
            else:
                missing.append("hand")
    else:
        if detect_faces:
            if _model_file_available(face_model):
                detectors.append(MediaPipeFaceDetector(face_model, confidence))
            else:
                missing.append("face")
        if detect_hands:
            if _model_file_available(hand_model):
                detectors.append(MediaPipeHandDetector(hand_model, confidence))
            else:
                missing.append("hand")
    return DetectorBuildResult(detectors, missing)


# -- orchestration --------------------------------------------------------


def detect_tracks(
    frames: np.ndarray,
    detectors: Sequence[FrameDetector],
    *,
    stride: int = 6,
    fps: float = 24.0,
    iou_threshold: float = 0.3,
    max_gap_steps: int = 1,
    cut_threshold: float = 0.35,
    min_track_seconds: float = 0.5,
    max_tracks: int = 4,
    merge_overlap: float = 0.6,
) -> List[Track]:
    """Run ``detectors`` over ``frames`` (``(T, H, W, 3)`` uint8) at ``stride``
    and build the final list of stabilized-ready tracks.

    Pipeline: sample every ``stride`` frames -> detect per kind -> greedy-IoU
    link into tracks -> split at histogram scene cuts -> drop tracks shorter
    than ``min_track_seconds`` -> merge coincident + cap to ``max_tracks``.
    Detection is the only model-touching step and is fully behind the
    ``detectors`` seam, so this is testable with stub detectors."""
    from PIL import Image

    total = int(frames.shape[0])
    sample_indices = list(range(0, total, max(1, int(stride))))
    if not sample_indices:
        return []

    # One PIL conversion per sampled frame, reused across every detector.
    per_kind: dict = {}
    for idx in sample_indices:
        pil = Image.fromarray(frames[idx])
        for det in detectors:
            boxes = det.detect(pil)
            if boxes:
                per_kind.setdefault(det.kind, {}).setdefault(
                    idx, []).extend(Detection(idx, tuple(float(v) for v in b), det.kind) for b in boxes)

    cuts = detect_scene_cuts(frames, sample_indices, threshold=cut_threshold)
    min_frames = max(1, int(round(min_track_seconds * fps)))

    all_tracks: List[Track] = []
    for kind, by_frame in per_kind.items():
        # Pass EVERY sampled index (not only frames that fired) so the linker's
        # gap tolerance counts in uniform sampled-frame steps -- a subject
        # missed at one stride is an empty step, not a hole in the sequence.
        frame_dets = [(idx, by_frame.get(idx, [])) for idx in sample_indices]
        tracks = link_detections(
            frame_dets, iou_threshold=iou_threshold, max_gap_steps=max_gap_steps)
        tracks = split_tracks_at_cuts(tracks, cuts)
        tracks = filter_short_tracks(tracks, min_frames)
        all_tracks.extend(tracks)

    final = cap_and_merge_tracks(all_tracks, max_tracks=max_tracks, merge_overlap=merge_overlap)
    logger.info(
        "[VIDEO_DETAILER] %d sampled frame(s), %d scene cut(s) -> %d track(s) after cap",
        len(sample_indices), len(cuts), len(final),
    )
    return final
