from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

Box = Tuple[float, float, float, float]
Point = Tuple[float, float]


@dataclass
class FaceDetection:
    box: Box
    landmarks: Optional[List[Point]] = field(default=None)
    confidence: Optional[float] = field(default=None)
    kind: str = field(default="face")

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.box
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


class FaceFrameDetector(ABC):
    @abstractmethod
    def detect(self, image: Image.Image) -> List[FaceDetection]:
        raise NotImplementedError


def _landmarks_bbox(points: Sequence[Point]) -> Box:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


class MediaPipeFaceDetector(FaceFrameDetector):
    def __init__(self, model_path: str, confidence: float = 0.5, max_faces: int = 10):
        self.model_path = model_path
        self.confidence = float(confidence)
        self.max_faces = int(max_faces)

    def detect(self, image: Image.Image) -> List[FaceDetection]:
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
            detections = []
            for landmarks in faces:
                points = [(lm.x * image.width, lm.y * image.height) for lm in landmarks]
                detections.append(FaceDetection(box=_landmarks_bbox(points), landmarks=points))
            return detections


class MediaPipeHandDetector(FaceFrameDetector):
    def __init__(self, model_path: str, confidence: float = 0.5, max_hands: int = 6):
        self.model_path = model_path
        self.confidence = float(confidence)
        self.max_hands = int(max_hands)

    def detect(self, image: Image.Image) -> List[FaceDetection]:
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
            detections = []
            for landmarks in hands:
                points = [(lm.x * image.width, lm.y * image.height) for lm in landmarks]
                detections.append(FaceDetection(box=_landmarks_bbox(points), kind="hand"))
            return detections

class YoloFaceDetector(FaceFrameDetector):
    def __init__(self, model_path: str, confidence: float = 0.5, device: str = "cuda", kind: str = "face"):
        self.model_path = model_path
        self.confidence = float(confidence)
        self.device = device
        self.kind = kind
        self._impl = None

    def _build(self):
        from src.pipelines.pipes._shared.detection.detailer_helper import DetailerHelper

        det_config = {
            "type": "yolo",
            "model": self.model_path,
            "confidence": self.confidence,
            "device": self.device,
        }
        helper = DetailerHelper({"device": self.device, "detections": {self.kind: det_config}})
        return helper, helper.load_detector(self.model_path)

    def detect(self, image: Image.Image) -> List[FaceDetection]:
        if self._impl is None:
            self._impl = self._build()
        helper, model = self._impl
        return [
            FaceDetection(box=tuple(float(v) for v in box), confidence=score, kind=self.kind)
            for box, score in helper.detect_objects_scored(image, model, self.kind)
        ]


def build_face_detector(
    backend: str,
    *,
    model_path: str,
    confidence: float = 0.5,
    max_faces: int = 10,
    device: str = "cuda",
) -> FaceFrameDetector:
    if backend == "yolo":
        return YoloFaceDetector(model_path, confidence, device=device)
    return MediaPipeFaceDetector(model_path, confidence, max_faces)

def build_hand_detector(
    backend: str,
    *,
    model_path: str,
    confidence: float = 0.5,
    max_hands: int = 6,
    device: str = "cuda",
) -> FaceFrameDetector:
    if backend == "yolo":
        return YoloFaceDetector(model_path, confidence, device=device, kind="hand")
    return MediaPipeHandDetector(model_path, confidence, max_hands)

class CombinedDetector(FaceFrameDetector):
    def __init__(self, detectors: Sequence[FaceFrameDetector]):
        self.detectors = list(detectors)

    def detect(self, image: Image.Image) -> List[FaceDetection]:
        found: List[FaceDetection] = []
        for detector in self.detectors:
            found.extend(detector.detect(image))
        return found
