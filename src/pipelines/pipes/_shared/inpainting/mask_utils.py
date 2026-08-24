"""
Mask processing utilities for inpainting pipes.

Provides functions for mask-based color correction and blending
used across multiple inpaint pipes.
"""

import numpy as np


def color_correction(inpainted_image, original_image, mask):
    """Apply color correction to blend inpainted region with original image.

    Uses weighted blending based on mask intensity to create smooth transitions
    between inpainted and original regions.

    Args:
        inpainted_image: Numpy array (H, W, C) of the inpainted result.
        original_image: Numpy array (H, W, C) of the original image.
        mask: Numpy array (H, W) with values 0-255 indicating blend weight.

    Returns:
        Color-corrected numpy array (H, W, C) with values clipped to 0-255.
    """
    fg = inpainted_image.astype(np.float32)
    bg = original_image.astype(np.float32)

    # Ensure mask has correct shape
    if mask.ndim == 2:
        mask = mask[:, :, None]

    w = mask.astype(np.float32) / 255.0
    y = fg * w + bg * (1 - w)

    return y.clip(0, 255).astype(np.uint8)
