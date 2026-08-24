"""
File service for managing file operations with unified storage structure.

This service handles file system operations with the new storage structure:
- storage/generations/ - Main generation outputs
- storage/tmp/ - Temporary files
- storage/models/ - Model media files
"""

import os
import logging
import mimetypes
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional, List, Tuple, Literal
from datetime import datetime

from src.platform.filesystem import audio_formats
from src.platform.filesystem.mesh_formats import mesh_format_registry
from src.platform.filesystem.storage_driver import (
    FileStorageDriver,
    LocalFileStorageDriver,
    _atomic_copy_file,
    _atomic_write_bytes,
    generations_key,
    local_copy,
)
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)

FileType = Literal['IMAGE', 'VIDEO', 'AUDIO', 'MESH']
StorageType = Literal['generations', 'tmp', 'models']


class FileStore:
    """Service for managing file operations with unified storage structure."""

    def __init__(self, base_storage_dir: str = None, storage_driver: Optional[FileStorageDriver] = None):
        """
        Initialize the file service.

        Args:
            base_storage_dir: Base directory for all file storage.
                            If None, checks POTIONUI_STORAGE_PATH environment variable,
                            defaults to "storage" if env var is not set.
            storage_driver: Where `storage_type='generations'` bytes actually
                live - local disk by default, optionally S3 (see
                `StorageSettingsManager`). `tmp`/`models` always write straight
                to `base_storage_dir`, unaffected by this - only generation
                output (final files + thumbnails) goes through it. Defaults to
                a `LocalFileStorageDriver` rooted at `base_storage_dir`, which
                makes every write land at the exact path this class always
                wrote it at.
        """
        # Support test storage isolation via environment variable
        if base_storage_dir is None:
            base_storage_dir = os.getenv('POTIONUI_STORAGE_PATH', 'storage')

        self.base_storage_dir = Path(base_storage_dir)
        self.base_storage_dir.mkdir(exist_ok=True)

        # Create subdirectories
        self.generations_dir = self.base_storage_dir / "generations"
        self.tmp_dir = self.base_storage_dir / "tmp"
        self.models_dir = self.base_storage_dir / "models"

        self.generations_dir.mkdir(exist_ok=True)
        self.tmp_dir.mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)

        self.storage_driver = storage_driver or LocalFileStorageDriver(str(self.base_storage_dir))
    
    def determine_file_type(self, extension: str) -> FileType:
        """Determine file type from extension."""
        ext = extension.lower().lstrip('.')
        if ext in ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff']:
            return 'IMAGE'
        elif ext in ['mp4', 'avi', 'mov', 'mkv', 'webm', 'gif']:
            return 'VIDEO'
        elif audio_formats.is_registered(ext):
            return 'AUDIO'
        elif mesh_format_registry.is_registered(f'.{ext}'):
            return 'MESH'
        else:
            # Default to IMAGE for unknown extensions
            return 'IMAGE'

    def get_mime_type(self, extension: str) -> str:
        """Get MIME type from file extension."""
        mime_type, _ = mimetypes.guess_type(f"file.{extension.lstrip('.')}")
        return mime_type or 'application/octet-stream'

    def get_relative_path(self, full_path: str) -> str:
        """
        Get relative path from the base storage directory.

        Args:
            full_path: Full filesystem path

        Returns:
            Relative path from storage directory
        """
        path = Path(full_path)
        try:
            return str(path.relative_to(self.base_storage_dir))
        except ValueError:
            # Path is not relative to base storage dir, return as-is
            return str(path)

    def get_full_path(self, relative_path: str) -> str:
        """
        Get full filesystem path from relative path.

        Args:
            relative_path: Relative path from storage directory

        Returns:
            Full filesystem path
        """
        return str(self.base_storage_dir / relative_path)

    def _generation_key(self, generation_id: str, extension: str, prefix: Optional[str]) -> str:
        """The storage key a `storage_type='generations'` write targets -
        the same `generations/<date>/<generation_id>/<filename>` convention
        `files.file_path` already uses, computed as a relative key directly
        rather than by joining `base_storage_dir` - so it lines up with
        `self.storage_driver`'s own key space even when this `FileStore` was
        built with a different local root than the driver's."""
        current_date = datetime.now().strftime('%Y-%m-%d')
        filename = f"{prefix}.{extension}" if prefix else f"{generate_ulid()}.{extension}"
        return f"generations/{current_date}/{generation_id}/{filename}"

    def _resolve_target_path(
        self,
        generation_id: Optional[str],
        extension: str,
        prefix: Optional[str],
        storage_type: StorageType,
    ) -> Path:
        """Resolve the destination path a `tmp`/`models` file should be
        saved at, creating its parent directory. `generations` writes never
        reach this - see `_generation_key` and `save_file`/`save_file_from_path`."""
        if storage_type == 'tmp':
            target_dir = self.tmp_dir
            target_dir.mkdir(exist_ok=True)
        elif storage_type == 'models':
            target_dir = self.models_dir
            target_dir.mkdir(exist_ok=True)
        else:
            raise ValueError(f"Invalid storage configuration: {storage_type}, generation_id={generation_id}")

        # For other files, include ULID for uniqueness
        if prefix:
            filename = f"{prefix}_{generate_ulid()}.{extension}"
        else:
            # No prefix, use ULID as filename
            filename = f"{generate_ulid()}.{extension}"

        return target_dir / filename

    def save_file(
        self,
        generation_id: str,
        file_data: bytes,
        extension: str = "png",
        prefix: Optional[str] = None,
        storage_type: StorageType = 'generations',
        is_temporary: bool = False
    ) -> Tuple[Optional[str], Optional[dict]]:
        """
        Save a file and return both full path and file metadata.

        `storage_type='generations'` writes go through `self.storage_driver`
        (local disk by default, optionally S3); `tmp`/`models` always write
        straight to `base_storage_dir`.

        Args:
            generation_id: The generation ID (can be None for non-generation files)
            file_data: The file data to save
            extension: File extension (default: png)
            prefix: Optional prefix for the filename
            storage_type: Type of storage ('generations', 'tmp', 'models')
            is_temporary: Whether this is a temporary file

        Returns:
            Tuple of (full_path, file_metadata) or (None, None) if failed
        """
        try:
            if storage_type == 'generations':
                if not generation_id:
                    raise ValueError("save_file requires generation_id for storage_type='generations'")
                key = self._generation_key(generation_id, extension, prefix)
                self.storage_driver.put_bytes(key, file_data)
                local_path = self.storage_driver.local_path(key)
                full_path = str(local_path) if local_path is not None else key
                relative_path = key
            else:
                file_path = self._resolve_target_path(generation_id, extension, prefix, storage_type)
                # Save file atomically - never leave a truncated file at file_path
                _atomic_write_bytes(file_path, file_data)
                full_path = str(file_path)
                relative_path = self.get_relative_path(full_path)

            # Create file metadata
            file_metadata = {
                'file_path': relative_path,
                'file_type': self.determine_file_type(extension),
                'mime_type': self.get_mime_type(extension),
                'file_size': len(file_data),
                'is_temporary': is_temporary
            }

            logger.debug(f"Saved file: {full_path} (relative: {relative_path})")
            return full_path, file_metadata

        except Exception as e:
            logger.error(f"Failed to save file: {str(e)}")
            return None, None

    def save_file_from_path(
        self,
        generation_id: Optional[str],
        source_path: str,
        extension: str = "mp4",
        prefix: Optional[str] = None,
        storage_type: StorageType = 'generations',
        is_temporary: bool = False
    ) -> Tuple[Optional[str], Optional[dict]]:
        """
        Copy the file at `source_path` into storage and return both full path
        and file metadata, same contract as `save_file`.

        Unlike `save_file`, this never holds the source file's bytes in
        memory - it streams disk-to-disk (see `_atomic_copy_file` /
        `FileStorageDriver.put_file`). Intended for large media (video)
        already sitting on disk, where the caller would otherwise read the
        whole file into a `bytes` object just to hand it to `save_file`.

        Args:
            generation_id: The generation ID (can be None for non-generation files)
            source_path: Path to the file to copy in
            extension: File extension (default: mp4)
            prefix: Optional prefix for the filename
            storage_type: Type of storage ('generations', 'tmp', 'models')
            is_temporary: Whether this is a temporary file

        Returns:
            Tuple of (full_path, file_metadata) or (None, None) if failed
        """
        try:
            if storage_type == 'generations':
                if not generation_id:
                    raise ValueError("save_file_from_path requires generation_id for storage_type='generations'")
                key = self._generation_key(generation_id, extension, prefix)
                file_size = self.storage_driver.put_file(key, Path(source_path))
                local_path = self.storage_driver.local_path(key)
                full_path = str(local_path) if local_path is not None else key
                relative_path = key
            else:
                file_path = self._resolve_target_path(generation_id, extension, prefix, storage_type)
                file_size = _atomic_copy_file(Path(source_path), file_path)
                full_path = str(file_path)
                relative_path = self.get_relative_path(full_path)

            file_metadata = {
                'file_path': relative_path,
                'file_type': self.determine_file_type(extension),
                'mime_type': self.get_mime_type(extension),
                'file_size': file_size,
                'is_temporary': is_temporary
            }

            logger.debug(f"Saved file (streamed from {source_path}): {full_path} (relative: {relative_path})")
            return full_path, file_metadata

        except Exception as e:
            logger.error(f"Failed to save file from path: {str(e)}")
            return None, None

    def generation_exists(self, relative_path: str) -> bool:
        """Whether a `generations/...` key exists, through `self.storage_driver`."""
        return self.storage_driver.exists(generations_key(relative_path))

    def delete_generation_output(self, relative_path: str) -> bool:
        """Delete one `generations/...` key (an output file or one of its
        thumbnails), through `self.storage_driver`. Returns whether something
        was actually deleted."""
        return self.storage_driver.delete(generations_key(relative_path))

    def delete_generation_outputs(self, relative_paths: List[str]) -> Tuple[int, int]:
        """Delete a known set of `generations/...` keys - the file itself plus
        whatever thumbnail paths accompany it - through `self.storage_driver`.

        Unlike the old directory-scan this replaces, this never lists a
        generation's storage location: object storage has no "list files
        under this prefix" primitive on `FileStorageDriver`, so the caller
        (which already has the `files` DB rows) supplies the exact keys to
        remove. A key with no bytes behind it (e.g. a thumbnail size that was
        never generated) is not a failure - `driver.delete()` reports it as
        `False` and it is silently skipped.

        Returns:
            Tuple of (deleted_count, failed_count)
        """
        deleted = 0
        failed = 0
        for relative_path in relative_paths:
            try:
                if self.delete_generation_output(relative_path):
                    deleted += 1
            except Exception as e:
                failed += 1
                logger.error(f"Failed to delete generation output {relative_path}: {str(e)}")
        return (deleted, failed)

    @contextmanager
    def local_copy_of(self, relative_path: str, suffix: str = "") -> Iterator[Path]:
        """A real filesystem path for a `generations/...` key's bytes, for
        callers (ffprobe, PIL, zip export, ...) that need one regardless of
        storage backend. See `storage_driver.local_copy`."""
        with local_copy(self.storage_driver, generations_key(relative_path), suffix) as path:
            yield path