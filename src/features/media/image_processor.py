"""
Image processing utilities for PotionUI.

This module provides image manipulation functions including
resizing and thumbnail generation.
"""

import io
import logging
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)


class ImageProcessor:
    """Handles image resizing and thumbnail generation."""

    # Default thumbnail width
    DEFAULT_THUMBNAIL_WIDTH = 150

    # JPEG quality for output
    JPEG_QUALITY = 85

    def resize_image(
        self,
        file_path: Path,
        width: Optional[int] = None,
        height: Optional[int] = None
    ) -> bytes:
        """Resize an image to specified dimensions.

        Args:
            file_path: Path to the image file
            width: Target width (maintains aspect ratio if height not specified)
            height: Target height (maintains aspect ratio if width not specified)

        Returns:
            Resized image as bytes

        Raises:
            ValueError: If the file cannot be processed
        """
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")

        try:
            img = Image.open(file_path)
            original_size = img.size

            # Calculate new dimensions
            new_size = self._calculate_dimensions(
                original_size[0],
                original_size[1],
                width,
                height
            )

            # Resize the image
            if new_size != original_size:
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Save to buffer
            img_buffer = io.BytesIO()
            img_format = self._get_save_format(file_path.suffix)
            img.save(img_buffer, format=img_format, quality=self.JPEG_QUALITY)
            img_buffer.seek(0)

            return img_buffer.getvalue()

        except Exception as e:
            logger.error(f"Failed to resize image {file_path}: {str(e)}")
            raise ValueError(f"Failed to resize image: {str(e)}")

    def generate_thumbnail(
        self,
        file_path: Path,
        width: int = DEFAULT_THUMBNAIL_WIDTH
    ) -> bytes:
        """Generate a thumbnail for an image.

        Args:
            file_path: Path to the image file
            width: Thumbnail width (height calculated to maintain aspect ratio)

        Returns:
            Thumbnail image as bytes

        Raises:
            ValueError: If the file cannot be processed
        """
        return self.resize_image(file_path, width=width)

    def get_image_dimensions(self, file_path: Path) -> Tuple[int, int]:
        """Get the dimensions of an image.

        Args:
            file_path: Path to the image file

        Returns:
            Tuple of (width, height)

        Raises:
            ValueError: If the file cannot be read
        """
        if not file_path.exists():
            raise ValueError(f"File not found: {file_path}")

        try:
            with Image.open(file_path) as img:
                return img.size
        except Exception as e:
            logger.error(f"Failed to get image dimensions for {file_path}: {str(e)}")
            raise ValueError(f"Failed to get image dimensions: {str(e)}")

    def _calculate_dimensions(
        self,
        original_width: int,
        original_height: int,
        target_width: Optional[int],
        target_height: Optional[int]
    ) -> Tuple[int, int]:
        """Calculate new dimensions maintaining aspect ratio.

        Args:
            original_width: Original image width
            original_height: Original image height
            target_width: Target width (optional)
            target_height: Target height (optional)

        Returns:
            Tuple of (new_width, new_height)
        """
        if target_width and target_height:
            # Both dimensions specified - use them directly
            return (target_width, target_height)
        elif target_width:
            # Only width specified - calculate height
            ratio = target_width / original_width
            new_height = int(original_height * ratio)
            return (target_width, new_height)
        elif target_height:
            # Only height specified - calculate width
            ratio = target_height / original_height
            new_width = int(original_width * ratio)
            return (new_width, target_height)
        else:
            # No dimensions specified - return original
            return (original_width, original_height)

    def _get_save_format(self, suffix: str) -> str:
        """Get PIL save format from file suffix.

        Args:
            suffix: File extension including dot

        Returns:
            PIL format string
        """
        suffix_lower = suffix.lower()
        if suffix_lower in ['.jpg', '.jpeg']:
            return 'JPEG'
        elif suffix_lower == '.png':
            return 'PNG'
        elif suffix_lower == '.webp':
            return 'WEBP'
        elif suffix_lower == '.bmp':
            return 'BMP'
        elif suffix_lower == '.gif':
            return 'GIF'
        else:
            # Default to JPEG for unknown types
            return 'JPEG'
