"""
Inpainting utility functions for mask processing and image preparation.
Based on Fooocus inpainting implementation.
"""

import numpy as np
from PIL import Image, ImageFilter
import cv2


def blur_mask(mask: np.ndarray, blur_radius: int) -> np.ndarray:
    """
    Apply Gaussian blur to mask edges for smoother blending.

    Args:
        mask: Binary mask array (HxW) with values 0-255
        blur_radius: Radius for Gaussian blur

    Returns:
        Blurred mask array
    """
    if blur_radius <= 0:
        return mask

    # Convert to PIL Image for blurring
    mask_pil = Image.fromarray(mask.astype(np.uint8))
    mask_pil = mask_pil.filter(ImageFilter.GaussianBlur(radius=blur_radius))

    return np.array(mask_pil)


def dilate_mask(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    """
    Dilate mask to expand the masked region.

    Args:
        mask: Binary mask array (HxW) with values 0-255
        iterations: Number of dilation iterations

    Returns:
        Dilated mask array
    """
    if iterations <= 0:
        return mask

    kernel = np.ones((3, 3), dtype=np.uint8)
    dilated = cv2.dilate(mask.astype(np.uint8), kernel, iterations=iterations)

    return dilated
