"""
Media module for PotionUI.

This module provides media functionality including:
- MediaStore: Orchestrates media operations with business logic
- MediaTypeResolver: Resolves file extensions to MIME types
- FilePathResolver: Securely resolves file paths
- ImageProcessor: Handles image resizing and thumbnails
"""

from src.features.media.store import (
    MediaStore,
    PRESET_THUMBNAIL_WIDTHS,
    UnsupportedSizeError,
)
from src.features.media.media_types import MediaTypeResolver
from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.upload_repository import UploadRepository

__all__ = [
    "MediaStore",
    "MediaTypeResolver",
    "FilePathResolver",
    "ImageProcessor",
    "PRESET_THUMBNAIL_WIDTHS",
    "UnsupportedSizeError",
    "UploadRepository",
]
