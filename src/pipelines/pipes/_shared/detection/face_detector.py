from typing import List, Dict, Any
import numpy as np
from PIL import Image

from .base_detector import BaseDetector


class FaceDetector(BaseDetector):
    """
    Face detection and enhancement using YOLO models.

    Features:
    - YOLO-based detection with configurable model
    - Adaptive step reduction for large faces
    - SDXL-optimized mask blending
    - Optional MediaPipe fallback
    """

    def __init__(self, config: Dict[str, Any], helper):
        super().__init__(config, helper)
        self._detector_model = None

    def get_detection_type(self) -> str:
        return "face"

    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect faces using YOLO or MediaPipe based on configuration type.
        """
        detection_type = self.config.get("type", "yolo").lower()

        # Use MediaPipe if configured
        if detection_type == "mediapipe":
            model_path = self.config.get("model", "models/mediapipe/face_landmarker.task")
            return self.helper.detect_mediapipe(image, model_path)

        # Use YOLO detection (default)
        if self._detector_model is None:
            model_path = self.config.get("model", "models/detection_bbox/face_yolov12m.pt")
            self._detector_model = self.helper.load_detector(model_path)

        return self.helper.detect_objects(image, self._detector_model, "face")

    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create SDXL-optimized adaptive mask with tighter focus for face enhancement.
        """
        # SDXL-specific adaptive mask with feather ratio 0.1 for tighter focus
        return self.helper.create_adaptive_mask(region_size, box, feather_ratio=0.1)

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Adjust inference steps based on face size.
        Large faces get reduced steps to avoid over-processing.
        """
        adjusted_steps = self.helper.adjust_steps_based_on_face_size(box, image_size)
        # Cap at 20 for SDXL efficiency
        return min(adjusted_steps, 20)
