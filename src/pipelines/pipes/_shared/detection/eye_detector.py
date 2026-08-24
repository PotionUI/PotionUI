from typing import List, Dict, Any
import numpy as np
from PIL import Image

from .base_detector import BaseDetector


class EyeDetector(BaseDetector):
    """
    Eye detection and enhancement using MediaPipe face mesh.

    Features:
    - MediaPipe-based eye landmark detection
    - Specialized elliptical eye region mask
    - Processes both eyes together for consistency
    - Low strength refinement for subtle eye fixes
    """

    def get_detection_type(self) -> str:
        return "eyes"

    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect eyes using MediaPipe face mesh.
        Returns bounding boxes that encompass both eyes for each face.
        """
        model_path = self.config.get("model", "models/mediapipe/face_landmarker.task")
        return self.helper.detect_eyes_mediapipe(image, model_path)

    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create specialized elliptical eye region mask for smooth blending.
        This helps avoid harsh rectangular edges around the eye area.
        """
        return self.helper.create_eye_region_mask(region_size, box)

    def filter_boxes(self, boxes: List[np.ndarray], image_size: tuple) -> List[np.ndarray]:
        """
        Eye detection uses MediaPipe which already filters by confidence.
        No additional size-based filtering needed.
        """
        return boxes

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Eye enhancement uses moderate fixed steps for stability.
        """
        return int(self.config.get("steps", 20))
