from typing import List, Dict, Any
import numpy as np
from PIL import Image

from .base_detector import BaseDetector


class HandDetector(BaseDetector):
    """
    Hand detection and enhancement using YOLO models.

    Features:
    - YOLO-based hand detection with configurable model
    - SDXL-optimized mask with better edge handling
    - Fixed step count (no adaptive adjustment)
    """

    def __init__(self, config: Dict[str, Any], helper):
        super().__init__(config, helper)
        self._detector_model = None

    def get_detection_type(self) -> str:
        return "hand"

    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect hands using YOLO (default) or MediaPipe based on configuration type.
        """
        detection_type = self.config.get("type", "yolo").lower()

        # MediaPipe support could be added here in the future
        if detection_type == "mediapipe":
            # Future: implement MediaPipe hand detection
            # For now, fall back to YOLO
            pass

        # Use YOLO detection (default)
        if self._detector_model is None:
            model_path = self.config.get("model", "models/detection_bbox/hand_yolov8n.pt")
            self._detector_model = self.helper.load_detector(model_path)

        return self.helper.detect_objects(image, self._detector_model, "hand")

    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create SDXL-specific hand mask with better edge handling.
        Uses slightly larger feather ratio (0.12) for smoother blending.
        """
        return self.helper.create_adaptive_mask(region_size, box, feather_ratio=0.12)

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Hand enhancement uses fixed steps (no adaptive adjustment).
        """
        return int(self.config.get("steps", 20))
