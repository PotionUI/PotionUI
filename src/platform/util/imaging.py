"""
Global image utilities for base64 conversion and resizing.

This module provides reusable image processing functions for various parts
of the application including chat, generation, and other modules.
"""

import base64
import glob
import io
import os
import re
import logging
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

_GENERATION_URL_PATTERN = re.compile(r"^/api/media/generations/([^/?#]+)/([^/?#]+)")


def convert_image_to_base64(
    image_data: Optional[str],
    max_dimension: int = 768,
    quality: int = 85
) -> Optional[str]:
    """Convert image path or data to base64 with optional resizing.

    If image_data is already base64, returns as-is.
    If it's a file path, reads, resizes if needed, and converts to base64.

    Args:
        image_data: Either a file path or base64 string
        max_dimension: Maximum width/height in pixels (default 768)
        quality: JPEG quality for compression (default 85)

    Returns:
        Base64-encoded image string or None on failure
    """
    if not image_data:
        return None

    # Check if it's already base64 (doesn't contain path separators and is long)
    if '/' not in image_data and '\\' not in image_data and len(image_data) > 200:
        return image_data

    # It's a file path - try to read and convert
    try:
        file_path = _resolve_image_path(image_data)
        if not file_path:
            logger.warning(f"[ImageUtils] Image file not found: {image_data}")
            return None

        # Open image with PIL for processing
        img = Image.open(file_path)
        original_size = (img.width, img.height)

        # Convert to RGB if needed (JPEG doesn't support alpha)
        img = _ensure_rgb(img)

        # Resize if larger than max dimension
        if img.width > max_dimension or img.height > max_dimension:
            img = resize_image(img, max_dimension)
            logger.debug(
                f"[ImageUtils] Resized image from {original_size} to "
                f"({img.width}, {img.height})"
            )

        # Save to buffer as JPEG with optimized settings
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=quality, optimize=True)
        base64_data = base64.b64encode(buffered.getvalue()).decode('utf-8')

        logger.debug(
            f"[ImageUtils] Converted image to base64: {file_path} "
            f"({len(base64_data)} chars)"
        )
        return base64_data

    except Exception as e:
        logger.error(f"[ImageUtils] Failed to convert image to base64: {e}")
        return None


def resize_image(image: Image.Image, max_dimension: int) -> Image.Image:
    """Resize image to fit within max dimension while preserving aspect ratio.

    Args:
        image: PIL Image to resize
        max_dimension: Maximum width or height in pixels

    Returns:
        Resized PIL Image (or original if already within bounds)
    """
    if image.width <= max_dimension and image.height <= max_dimension:
        return image

    ratio = max_dimension / max(image.width, image.height)
    new_width = int(image.width * ratio)
    new_height = int(image.height * ratio)

    return image.resize((new_width, new_height), Image.LANCZOS)


def _resolve_image_path(image_data: str) -> Optional[str]:
    """Resolve image path, trying common base directories.

    Also recognizes the canonical generation media URL form
    ``/api/media/generations/{generation_id}/{filename}`` — the frontend passes
    this URL whenever it references a generated image (e.g. auto-attached last
    image in chat). Storage layout is ``storage/generations/YYYY-MM-DD/<ulid>/``
    so the date is unknown from the URL; we glob on the ULID.

    Args:
        image_data: Relative or absolute file path, or a generation media URL

    Returns:
        Resolved absolute path if found, None otherwise
    """
    match = _GENERATION_URL_PATTERN.match(image_data)
    if match:
        generation_id, filename = match.group(1), match.group(2)
        for base in ("storage/generations", "generations"):
            matches = glob.glob(os.path.join(base, "*", generation_id, filename))
            if matches:
                return matches[0]
        return None

    if os.path.isabs(image_data):
        return image_data if os.path.exists(image_data) else None

    # Try common base directories
    possible_paths = [
        image_data,
        os.path.join('storage', image_data),
        os.path.join('.', image_data),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    return None


def _ensure_rgb(image: Image.Image) -> Image.Image:
    """Ensure image is in RGB mode for JPEG compatibility.

    Args:
        image: PIL Image to convert

    Returns:
        Image in RGB mode
    """
    if image.mode == 'RGBA':
        return image.convert('RGB')
    elif image.mode not in ('RGB', 'L'):
        return image.convert('RGB')
    return image
