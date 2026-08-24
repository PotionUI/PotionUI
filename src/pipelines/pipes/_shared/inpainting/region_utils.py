"""
Region computation utilities for inpainting pipes.

Provides functions for computing crop regions around masked areas,
resizing images with dimension alignment, and high-quality resampling.
Based on Fooocus InpaintWorker implementation.
"""

import numpy as np
from PIL import Image


def compute_initial_abcd(mask):
    """Compute initial bounding box around masked region.

    Finds the smallest bounding box containing all non-zero pixels in the mask,
    then expands it by 15% to ensure smooth transitions at the edges.

    Args:
        mask: 2D numpy array (HxW) with mask values.

    Returns:
        Tuple of (top, bottom, left, right) pixel coordinates, clamped to image bounds.
    """
    indices = np.where(mask > 0)
    if len(indices[0]) == 0:
        # No mask, return full image bounds
        return 0, mask.shape[0], 0, mask.shape[1]

    a = np.min(indices[0])
    b = np.max(indices[0])
    c = np.min(indices[1])
    d = np.max(indices[1])

    # Expand by 15% to ensure smooth transitions
    abp = (b + a) // 2
    abm = (b - a) // 2
    cdp = (d + c) // 2
    cdm = (d - c) // 2
    l = int(max(abm, cdm) * 1.15)

    a = abp - l
    b = abp + l + 1
    c = cdp - l
    d = cdp + l + 1

    # Clamp to image bounds
    H, W = mask.shape[:2]
    a = max(0, min(H, a))
    b = max(0, min(H, b))
    c = max(0, min(W, c))
    d = max(0, min(W, d))

    return int(a), int(b), int(c), int(d)


def solve_abcd(mask, a, b, c, d, k=0.618):
    """Expand the bounding box to cover at least k fraction of the image.

    Iteratively grows the bounding box until it covers at least k fraction
    of both the height and width of the image. This ensures enough context
    is available for high-quality inpainting.

    Args:
        mask: 2D numpy array (HxW) used only for its shape.
        a: Top of bounding box.
        b: Bottom of bounding box.
        c: Left of bounding box.
        d: Right of bounding box.
        k: Minimum fraction of image dimensions to cover (0.0 to 1.0).

    Returns:
        Tuple of (top, bottom, left, right) pixel coordinates, clamped to image bounds.
    """
    k = float(k)
    assert 0.0 <= k <= 1.0

    H, W = mask.shape[:2]

    if k == 1.0:
        return 0, H, 0, W

    while True:
        if b - a >= H * k and d - c >= W * k:
            break

        add_h = (b - a) < (d - c)
        add_w = not add_h

        if b - a == H:
            add_w = True

        if d - c == W:
            add_h = True

        if add_h:
            a -= 1
            b += 1

        if add_w:
            c -= 1
            d += 1

        # Clamp to bounds
        a = max(0, min(H, a))
        b = max(0, min(H, b))
        c = max(0, min(W, c))
        d = max(0, min(W, d))

    return int(a), int(b), int(c), int(d)


def get_image_shape_ceil(image):
    """Get the maximum dimension of the image.

    Args:
        image: Numpy array with shape (H, W, ...) or (H, W).

    Returns:
        The larger of the two spatial dimensions.
    """
    H, W = image.shape[:2]
    return max(H, W)


def set_image_shape_ceil(image, max_size):
    """Resize image so maximum dimension is max_size, ensuring divisibility by 8.

    If the image is already within max_size, only aligns dimensions to
    multiples of 8 (required by diffusion models). Uses Lanczos resampling.

    Args:
        image: Numpy array (H, W, C) or (H, W).
        max_size: Target maximum dimension.

    Returns:
        Resized numpy array with dimensions divisible by 8.
    """
    H, W = image.shape[:2]
    current_max = max(H, W)

    if current_max <= max_size:
        # Still need to ensure divisibility by 8
        new_H = (H // 8) * 8
        new_W = (W // 8) * 8
        if new_H == H and new_W == W:
            return image
    else:
        scale = max_size / current_max
        new_H = int(H * scale)
        new_W = int(W * scale)

    # Ensure dimensions are divisible by 8 (required for diffusion models)
    new_H = (new_H // 8) * 8
    new_W = (new_W // 8) * 8

    # Use PIL for high-quality resizing
    pil_img = Image.fromarray(image)
    pil_img = pil_img.resize((new_W, new_H), Image.LANCZOS)
    return np.array(pil_img)


def resample_image(image, width, height):
    """Resample image to target size using high-quality Lanczos resampling.

    Args:
        image: Numpy array (H, W, C) or (H, W).
        width: Target width.
        height: Target height.

    Returns:
        Resampled numpy array.
    """
    pil_img = Image.fromarray(image)
    pil_img = pil_img.resize((width, height), Image.LANCZOS)
    return np.array(pil_img)
