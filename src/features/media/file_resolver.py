"""
File path resolution for PotionUI.

This module provides secure path resolution for media files,
including security validation against directory traversal attacks.
"""

import logging
import os
from pathlib import Path, PurePosixPath
from typing import Optional, Any


from src.features.media.media_types import MediaTypeResolver
from src.platform.settings.settings import Settings

logger = logging.getLogger(__name__)

# The one directory of a preset that may be served over the media route. Everything
# else - preset.yml at the root, the option YAML and ComfyUI workflows under files/ -
# is read server-side and must never be reachable over HTTP.
PRESET_PUBLIC_ROOT = "public"

# `.svg` is deliberately dropped from IMAGE_EXTENSIONS: an SVG served inline can
# carry script, and preset content may come from a marketplace.
PRESET_SERVABLE_EXTENSIONS = frozenset(
    (
        MediaTypeResolver.IMAGE_EXTENSIONS
        | MediaTypeResolver.VIDEO_EXTENSIONS
        | MediaTypeResolver.AUDIO_EXTENSIONS
    ) - {".svg"}
)


class FilePathResolver:
    """Resolves and validates file paths securely."""

    def __init__(
        self,
        settings: Settings,
        preset_loader: Optional[Any] = None
    ):
        """Initialize FilePathResolver.

        Args:
            settings: Settings manager for directory configuration
            preset_loader: Optional preset loader for preset file resolution
        """
        self.settings = settings
        self.preset_loader = preset_loader

    def resolve_temp_file(self, filename: str, user_id: Optional[str] = None) -> Path:
        """Resolve path to a temporary file.

        Args:
            filename: File name
            user_id: Optional user ID for storage directory

        Returns:
            Resolved file path

        Raises:
            ValueError: If path validation fails
        """
        storage_dir = self.settings.get_file_storage_directory(user_id)
        temp_dir = Path(storage_dir) / "tmp"
        temp_path = temp_dir / filename

        # Security check
        if not self.validate_path_security(temp_path, temp_dir):
            raise ValueError("Access denied - path traversal detected")

        return temp_path.resolve()

    def resolve_upload_file(self, filename: str, user_id: Optional[str] = None) -> Path:
        """Resolve path to an uploaded file.

        Args:
            filename: File name
            user_id: User ID for storage directory

        Returns:
            Resolved file path

        Raises:
            ValueError: If path validation fails
        """
        storage_dir = self.settings.get_file_storage_directory(user_id)
        uploads_dir = Path(storage_dir) / "uploads"
        file_path = uploads_dir / filename

        # Security check
        if not self.validate_path_security(file_path, uploads_dir):
            raise ValueError("Access denied - path traversal detected")

        return file_path.resolve()

    def resolve_preset_file(self, preset_id: str, file_path: str) -> Path:
        """Resolve path to a servable media file inside a preset directory.

        This is the single choke point for every preset-asset request, so the
        root and extension allowlists live here rather than in the callers.

        Args:
            preset_id: Preset identifier
            file_path: Relative path within preset directory

        Returns:
            Resolved file path

        Raises:
            ValueError: If preset not found, path validation fails, or the file
                is not a servable asset. The message is deliberately uniform so
                a caller cannot distinguish "wrong type" from "does not exist".
        """
        if not self.preset_loader:
            raise ValueError("Preset loader not configured")

        # Find preset by ID
        preset = None
        for preset_template in self.preset_loader.presets:
            if preset_template.id == preset_id:
                preset = preset_template
                break

        if not preset:
            raise ValueError(f"Preset not found: {preset_id}")

        # Everything servable lives under `public/`. preset.yml sits at the preset
        # root and is excluded by this check alone.
        posix_parts = PurePosixPath(file_path).parts
        if not posix_parts or posix_parts[0] != PRESET_PUBLIC_ROOT:
            logger.warning(f"Preset asset outside public/: {preset_id}/{file_path}")
            raise ValueError("Preset file not found")

        # Extension allowlist. Blocks .yml/.md smuggled into an allowed subdir, and
        # excludes .svg, which would be an XSS vector when served inline.
        if Path(file_path).suffix.lower() not in PRESET_SERVABLE_EXTENSIONS:
            logger.warning(f"Preset asset with disallowed extension: {preset_id}/{file_path}")
            raise ValueError("Preset file not found")

        # Construct full path
        preset_dir = Path(preset.path)
        full_file_path = preset_dir / file_path

        # Security check
        if not self.validate_path_security(full_file_path, preset_dir):
            logger.warning(f"Path traversal attempt detected: {file_path}")
            raise ValueError("Access denied - path traversal detected")

        return full_file_path.resolve()

    def validate_path_security(self, path: Path, allowed_base: Path) -> bool:
        """Validate that a path is within the allowed base directory.

        Args:
            path: Path to validate
            allowed_base: Base directory that path must be within

        Returns:
            True if path is valid and within allowed_base
        """
        try:
            # `is_relative_to`, not `str.startswith`: the latter accepts a sibling
            # whose name merely extends the base (".../standard-evil" passes for
            # base ".../standard").
            return path.resolve().is_relative_to(allowed_base.resolve())
        except Exception:
            return False

    def get_thumbnail_path(
        self,
        file_record: Any,
        size: str,
        animated: bool = False
    ) -> Optional[Path]:
        """Get thumbnail path for a file record.

        Args:
            file_record: Database file record with thumbnail paths
            size: Thumbnail size ('small', 'medium', 'large')
            animated: Whether to get animated thumbnail (for videos)

        Returns:
            Path to thumbnail, or None if not available
        """
        if size not in ['small', 'medium', 'large']:
            return None

        thumbnail_attr = f'thumbnail_{size}'
        thumbnail_path = getattr(file_record, thumbnail_attr, None)

        if not thumbnail_path:
            return None

        # Handle animated thumbnails for videos
        if animated and getattr(file_record, 'file_type', None) == 'VIDEO':
            thumbnail_dir = Path(thumbnail_path).parent
            thumbnail_name = Path(thumbnail_path).stem
            animated_path = thumbnail_dir / f"{thumbnail_name}_animated.webp"
            return animated_path

        return Path(thumbnail_path)

    def get_uploads_directory(self, user_id: Optional[str] = None) -> Path:
        """Get the uploads directory path.

        Args:
            user_id: User ID for storage directory

        Returns:
            Path to uploads directory
        """
        storage_dir = self.settings.get_file_storage_directory(user_id)
        uploads_dir = Path(storage_dir) / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        return uploads_dir

    def get_storage_directory(self, user_id: Optional[str] = None) -> str:
        """Get the base storage directory.

        Args:
            user_id: User ID for storage directory

        Returns:
            Storage directory path string
        """
        return self.settings.get_file_storage_directory(user_id)
