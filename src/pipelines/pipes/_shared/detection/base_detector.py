from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple
import numpy as np
from PIL import Image


class BaseDetector(ABC):
    """
    Abstract base class for all detector types.
    Each detector implements specific detection logic and configuration.
    """

    def __init__(self, config: Dict[str, Any], helper):
        """
        Initialize the detector with configuration and helper utilities.

        Args:
            config: Detection-specific configuration dictionary
            helper: DetailerHelper instance for utility methods
        """
        self.config = config
        self.helper = helper

    @abstractmethod
    def get_detection_type(self) -> str:
        """
        Return the detection type identifier (e.g., 'face', 'hand', 'eyes', 'teeth').
        """
        pass

    @abstractmethod
    def detect(self, image: Image.Image) -> List[np.ndarray]:
        """
        Detect objects in the image and return bounding boxes.

        Args:
            image: PIL Image to process

        Returns:
            List of bounding boxes as numpy arrays [x1, y1, x2, y2]
        """
        pass

    @abstractmethod
    def create_mask(self, region_size: tuple, box: List[int], region_img: Image.Image) -> Image.Image:
        """
        Create a mask for the detected region.
        Different detection types may use different mask strategies.

        Args:
            region_size: Size of the region (width, height)
            box: Bounding box coordinates [x1, y1, x2, y2]
            region_img: The region image for context

        Returns:
            PIL Image mask
        """
        pass

    def get_visualization_color(self) -> tuple:
        """
        Get the color for visualization boxes (RGB tuple).
        """
        return tuple(self.config.get("box_color", [0, 0, 0]))

    def get_box_thickness(self) -> int:
        """
        Get the thickness for visualization boxes.
        """
        return int(self.config.get("box_thickness", 8))

    def filter_boxes(self, boxes: List[np.ndarray], image_size: tuple) -> List[np.ndarray]:
        """
        Filter bounding boxes based on size ratios and other criteria.
        Default implementation uses mask_min_ratio and mask_max_ratio.
        Can be overridden for custom filtering logic.

        Args:
            boxes: List of bounding boxes
            image_size: Size of the image (width, height)

        Returns:
            Filtered list of bounding boxes
        """
        return self.helper.filter_boxes_by_ratio(boxes, image_size, self.get_detection_type())

    def should_upscale_region(self, region_size: Tuple[int, int]) -> bool:
        """
        Determine if a region should be upscaled before enhancement.

        Args:
            region_size: Size of the region (width, height)

        Returns:
            True if region should be upscaled
        """
        return self.helper.should_upscale_region(region_size, self.get_detection_type())

    def adjust_steps(self, box: np.ndarray, image_size: tuple) -> int:
        """
        Adjust the number of inference steps based on detection size.
        Default implementation returns configured steps without adjustment.
        Can be overridden for adaptive step calculation.

        Args:
            box: Bounding box coordinates
            image_size: Size of the image (width, height)

        Returns:
            Number of inference steps
        """
        return int(self.config.get("steps", 25))
