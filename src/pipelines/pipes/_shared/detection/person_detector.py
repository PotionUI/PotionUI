from typing import List, Dict, Any
import numpy as np
from PIL import Image

from .base_detector import BaseDetector


class PersonDetector(BaseDetector):
    """
    Full person detection and enhancement using YOLO models.

    Features:
    - YOLO-based detection for full body/person
    - Optimized for person segmentation and enhancement
    - Adaptive processing based on person size
    - Configurable strength and quality settings
    """

    def __init__(self, config: Dict[str, Any], helper):
        super().__init__(config, helper)
        self._detector_model = None

    def get_detection_type(self) -> str:
        return "person"

    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect persons using YOLOv8 COCO model with class filtering.
        Uses standard YOLOv8 model and filters for person class (class 0 in COCO dataset).
        """
        detection_type = self.config.get("type", "yolo").lower()

        # Person detection uses standard YOLOv8 COCO model
        if self._detector_model is None:
            # Use standard YOLOv8 model (auto-downloads if not present)
            model_path = self.config.get("model", "yolov8m.pt")
            self._detector_model = self.helper.load_detector(model_path)

        # Filter to only detect person class (class 0 in COCO dataset)
        return self.helper.detect_objects(image, self._detector_model, "person", classes=[0])

    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create adaptive mask for person enhancement with moderate feathering.
        """
        # Use moderate feather ratio (0.15) for smooth blending with background
        return self.helper.create_adaptive_mask(region_size, box, feather_ratio=0.15)

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Adjust inference steps based on person size.
        Larger persons may need fewer steps to avoid over-processing.
        """
        base_steps = int(self.config.get("steps", 20))

        # Calculate person area ratio
        box_width = box[2] - box[0]
        box_height = box[3] - box[1]
        box_area = box_width * box_height
        image_area = image_size[0] * image_size[1]
        area_ratio = box_area / image_area

        # Reduce steps for very large detections (> 50% of image)
        if area_ratio > 0.5:
            return max(int(base_steps * 0.7), 10)

        return base_steps
