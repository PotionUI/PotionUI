"""
Shared media helpers for WebSocket output serializers.
"""

import io
import base64
import logging
from typing import Optional

from PIL import Image

from src.features.generation.image_encode_cache import get_or_encode

logger = logging.getLogger(__name__)


def create_base64_image(image: Image.Image, max_dimension: int = 768) -> Optional[str]:
    """
    Create a base64-encoded image with downscaling for browser performance.

    Memoized per (image identity, max_dimension): the same PIL Image is
    commonly emitted twice (temporary preview, then final) -- see
    `image_encode_cache` for why the encode is skipped on the second call.

    Args:
        image: PIL Image to encode
        max_dimension: Maximum width/height in pixels

    Returns:
        Base64-encoded image string, or None if encoding failed.
    """
    return get_or_encode(image, max_dimension, _encode_base64_image)


def _encode_base64_image(image: Image.Image, max_dimension: int) -> Optional[str]:
    try:
        # Convert to RGB if image has alpha channel (JPEG doesn't support alpha)
        img_to_save = image
        if img_to_save.mode == 'RGBA':
            img_to_save = img_to_save.convert('RGB')

        # Check if image is larger than max dimension and resize if needed
        if img_to_save.width > max_dimension or img_to_save.height > max_dimension:
            ratio = max_dimension / max(img_to_save.width, img_to_save.height)
            new_width = int(img_to_save.width * ratio)
            new_height = int(img_to_save.height * ratio)
            img_to_save = img_to_save.resize((new_width, new_height), Image.LANCZOS)

        # Save with optimized settings
        buffered = io.BytesIO()
        img_to_save.save(buffered, format="JPEG", quality=85, optimize=True)
        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to create base64 image: {str(e)}")
        return None
