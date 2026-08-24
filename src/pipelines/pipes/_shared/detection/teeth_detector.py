from typing import List, Dict, Any
import numpy as np
from PIL import Image

from .base_detector import BaseDetector


class TeethDetector(BaseDetector):
    """
    Teeth detection and enhancement using MediaPipe face mesh.

    Features:
    - MediaPipe-based mouth landmark detection
    - Only processes when mouth is open (configurable threshold)
    - Precise inner lip area targeting for teeth
    - Very low strength for subtle teeth refinement
    """

    def get_detection_type(self) -> str:
        return "teeth"

    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect teeth using MediaPipe face mesh.
        Only returns bounding boxes when mouth is open enough to show teeth.
        """
        model_path = self.config.get("model", "models/mediapipe/face_landmarker.task")
        return self.helper.detect_teeth_mediapipe(image, model_path)

    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create adaptive mask for teeth region with tight focus.
        Uses feather ratio 0.1 for precise teeth area targeting.
        """
        return self.helper.create_adaptive_mask(region_size, box, feather_ratio=0.1)

    def filter_boxes(self, boxes: List[np.ndarray], image_size: tuple) -> List[np.ndarray]:
        """
        Teeth detection uses MediaPipe with mouth-open threshold.
        No additional size-based filtering needed.
        """
        return boxes

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Teeth enhancement uses moderate steps for quality.
        """
        return int(self.config.get("steps", 20))
