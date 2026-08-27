"""
Media manager - business logic layer for media operations.

This module provides the MediaStore class that orchestrates all media-related
business logic, including file serving, uploads, and plugin hook execution.
"""

import hashlib
import io
import logging
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from PIL import Image

from src.features.media.file_resolver import FilePathResolver
from src.features.media.image_processor import ImageProcessor
from src.features.media.media_types import MediaTypeResolver
from src.platform.plugins import PluginRegistry
from src.platform.plugins.hooks import execute_hook
from src.features.media.hooks import MEDIA_HOOKS
from src.platform.settings.settings import Settings
from src.features.generation.file_repository import FileRepository
from src.features.generation.repository import GenerationRepository
from src.features.generation import media_probe
from src.features.generation.handlers.image_handler import generate_thumbnails
from src.features.generation.handlers.video_handler import generate_video_thumbnails
from src.platform.filesystem.file_store import FileStore
from src.platform.filesystem.storage_driver import (
    FileStorageDriver,
    LocalFileStorageDriver,
    StorageKeyError,
    validate_key,
)
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.media.validators import UPLOAD_PURPOSE_USER, validate_upload_purpose_policy
from src.features.media.dto import (
    MediaResult,
    UploadResult,
    UploadInfoResult,
    MediaFileInfo,
    MediaListResult,
    DeleteResult,
    FileParamsResult,
    UploadFileInfo,
    UploadListResult,
)

logger = logging.getLogger(__name__)

# Render widths for preset assets. Same ladder as the generation thumbnails,
# but rendered on demand rather than at write time,
# because a preset has no `files` row to hang derivative paths off.
# `thumbnail` is the pre-existing name and keeps its original 150px width.
PRESET_THUMBNAIL_WIDTHS = {
    "thumbnail": 150,
    "small": 480,
    "medium": 768,
    "large": 1024,
}


class UnsupportedSizeError(ValueError):
    """Raised when `?size=` names a render size that does not exist."""


# Hard ceiling on `?limit=` for the upload library list, independent of whatever
# the caller asks for, so a stray huge value can't turn a paginated list into an
# unbounded scan.
MAX_UPLOAD_LIST_LIMIT = 100


class MediaStore:
    """
    Coordinates media operations - business logic layer.

    Responsibilities:
    - File serving and validation
    - Upload handling
    - Hook execution for plugin integration
    - Path resolution and security
    """

    def __init__(
        self,
        file_resolver: FilePathResolver,
        image_processor: ImageProcessor,
        media_type_resolver: MediaTypeResolver,
        file_repository: FileRepository,
        generation_repository: GenerationRepository,
        settings: Settings,
        file_service: FileStore,
        plugin_registry: PluginRegistry,
        upload_repository: Optional[UploadRepository] = None,
        storage_driver: Optional[FileStorageDriver] = None,
    ):
        """Initialize MediaStore.

        Args:
            file_resolver: Path resolution service
            image_processor: Image manipulation service
            media_type_resolver: MIME type detection
            file_repository: Repository for file data access
            generation_repository: Repository for generation data access
            settings: Settings manager
            file_service: File service for file operations
            plugin_registry: Plugin registry for hook execution
            upload_repository: Repository for upload ownership/metadata
            storage_driver: Where upload bytes actually live - local disk by
                default, optionally S3 (see `StorageSettings`). Only
                the upload lifecycle (upload/serve/delete) goes through this;
                generation output still writes through `file_service` - see
                `docs/s3-storage.md`.
        """
        self.file_resolver = file_resolver
        self.image_processor = image_processor
        self.media_types = media_type_resolver
        self.file_repo = file_repository
        self.generation_repo = generation_repository
        self.settings = settings
        self.file_service = file_service
        self.plugins = plugin_registry
        self.upload_repo = upload_repository or UploadRepository()
        self.storage_driver = storage_driver or LocalFileStorageDriver(
            settings.get_file_storage_directory()
        )

    # ========== Generation Media ==========

    def get_generation_media(
        self,
        generation_id: str,
        filename: str,
        user_id: Optional[str] = None,
        size: Optional[str] = None,
        animated: bool = False
    ) -> MediaResult:
        """Get media file from a generation.

        Args:
            generation_id: Generation identifier
            filename: Media filename
            user_id: Optional user ID for access control
            size: Optional size (small/medium/large) for thumbnails
            animated: Whether to serve animated thumbnail for videos

        Returns:
            MediaResult with file info

        Raises:
            ValueError: If generation or file not found
        """
        # Execute before_serve hook
        hook_data, blocked = execute_hook(self.plugins,
            MEDIA_HOOKS.before_serve,
            {
                "generation_id": generation_id,
                "filename": filename,
                "size": size
            }
        )

        if blocked:
            raise ValueError(hook_data.get("block_reason", "Media serve blocked"))

        # Get generation (allow access if user_id is None for public access)
        generation = self.generation_repo.get_by_id(generation_id, user_id=None)
        if not generation:
            raise ValueError("Generation not found")

        # Get files from generation
        files = self.generation_repo.get_files(generation_id, is_final=True)

        # Find the matching file
        target_file = None
        for file_record in files:
            if Path(file_record.file_path).name == filename:
                target_file = file_record
                break

        if not target_file:
            raise ValueError("File not found in database")

        # Handle thumbnail requests
        if size and size in ['small', 'medium', 'large']:
            thumbnail_path = self.file_resolver.get_thumbnail_path(
                target_file, size, animated
            )
            if not thumbnail_path:
                raise ValueError(f"Thumbnail size '{size}' not available")

            # Key relative to the file's own generation directory
            base_key = Path(target_file.file_path).parent
            key = (base_key / thumbnail_path).as_posix()
            result_filename = thumbnail_path.name
        else:
            # Serve original file
            key = target_file.file_path
            result_filename = filename

        file_size = self.storage_driver.size(key)
        if file_size is None:
            raise ValueError("File not found on disk")

        # Determine media type
        media_type = self.media_types.get_media_type(Path(result_filename).suffix)

        # Generate ETag
        etag_suffix = f"-{size}" if size else ""
        etag = f'"{generation_id}-{filename}{etag_suffix}"'

        headers = {
            "Cache-Control": "public, max-age=3600",
            "ETag": etag,
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Disposition": f'inline; filename="{result_filename}"'
        }

        local_path = self.storage_driver.local_path(key)
        if local_path is not None:
            return MediaResult(
                file_path=str(local_path),
                media_type=media_type,
                headers=headers,
                use_streaming=True
            )

        return MediaResult(
            content=self.storage_driver.get_bytes(key),
            media_type=media_type,
            headers=headers,
            use_streaming=False
        )

    # ========== Temporary Media ==========

    def get_temp_media(self, filename: str) -> MediaResult:
        """Get temporary media file.

        Args:
            filename: Temporary file name

        Returns:
            MediaResult with file info

        Raises:
            ValueError: If file not found or access denied
        """
        temp_path = self.file_resolver.resolve_temp_file(filename)

        if not temp_path.exists():
            raise ValueError("Temporary file not found")

        media_type = self.media_types.get_media_type(temp_path.suffix)

        return MediaResult(
            file_path=str(temp_path),
            media_type=media_type,
            headers={},
            use_streaming=False
        )

    # ========== Uploaded Media ==========

    def _upload_key(self, filename: str) -> str:
        """The storage key for an uploaded file's `filename`.

        Same containment guarantee `FilePathResolver.resolve_upload_file` gave
        a filesystem path - `validate_key` raises on traversal/absolute input,
        translated to the `ValueError` callers already expect.
        """
        try:
            return validate_key(f"uploads/{filename}")
        except StorageKeyError:
            raise ValueError("Access denied - path traversal detected")

    def _resolve_upload_thumbnail_key(self, filename: str, size: str, animated: bool) -> Optional[str]:
        """The storage key for one of `filename`'s thumbnails, or None if it
        doesn't have one at this size.

        `Upload` has no counterpart to `FilePathResolver.get_thumbnail_path` -
        that helper keys its animated-video branch off `File.file_type`
        ('VIDEO'), and `Upload` carries `media_type` ('video') instead - so
        this resolves the same shape directly rather than forcing a shared
        base type onto two records with nothing else in common.
        """
        upload = self.upload_repo.get_by_filename_unscoped(filename)
        if not upload:
            return None

        thumbnail_path = getattr(upload, f'thumbnail_{size}', None)
        if not thumbnail_path:
            return None

        if animated and (upload.media_type or '').lower() == 'video':
            thumbnail_path = str(Path(thumbnail_path).parent / f"{Path(thumbnail_path).stem}_animated.webp")

        return self._upload_key(thumbnail_path)

    def get_uploaded_media(
        self,
        filename: str,
        user_id: Optional[str] = None,
        size: Optional[str] = None,
        animated: bool = False,
    ) -> MediaResult:
        """Get uploaded media file, or one of its thumbnails.

        Args:
            filename: Uploaded file name
            user_id: User ID for storage directory
            size: Optional size (small/medium/large) for thumbnails
            animated: Whether to serve the animated (video) thumbnail

        Returns:
            MediaResult with file info

        Raises:
            ValueError: If file not found or access denied
        """
        if size and size in ('small', 'medium', 'large'):
            key = self._resolve_upload_thumbnail_key(filename, size, animated)
            if not key:
                raise ValueError(f"Thumbnail size '{size}' not available")
            result_filename = Path(key).name
        else:
            key = self._upload_key(filename)
            result_filename = filename

        if not self.storage_driver.exists(key):
            raise ValueError("Uploaded file not found")

        media_type = self.media_types.get_media_type(Path(result_filename).suffix)

        local_path = self.storage_driver.local_path(key)
        if local_path is not None:
            return MediaResult(
                file_path=str(local_path),
                media_type=media_type,
                headers={},
                use_streaming=False
            )

        return MediaResult(
            content=self.storage_driver.get_bytes(key),
            media_type=media_type,
            headers={},
            use_streaming=False
        )

    def get_upload_info(self, filename: str, user_id: Optional[str] = None) -> UploadInfoResult:
        """Best-effort metadata for an already-uploaded file.

        Resolves through the same containment-checked `_upload_key` the
        serving route uses - no naive path handling - then probes the same
        way a fresh upload does. Uploads have no `files` DB row, so there is
        nothing to resolve metadata FROM other than the file itself.

        Args:
            filename: Uploaded file name
            user_id: User ID for storage directory

        Returns:
            UploadInfoResult with best-effort metadata (fields None if not determined)

        Raises:
            ValueError: If file not found or access denied
        """
        key = self._upload_key(filename)

        size = self.storage_driver.size(key)
        if size is None:
            raise ValueError("Uploaded file not found")

        with self._local_copy(key, Path(filename).suffix) as local_path:
            width, height, duration_seconds, fps = self._probe_upload_metadata(local_path)

        return UploadInfoResult(
            filename=filename,
            size=size,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=fps
        )

    @contextmanager
    def _local_copy(self, key: str, suffix: str):
        """A real filesystem path for `key`'s bytes, for callers (ffprobe,
        PIL, ...) that need one regardless of storage backend.

        The local driver already has one - handed out directly, nothing
        copied. Any other driver has no local file, so its bytes are
        materialized into a temp file for the duration of the `with` block
        and removed on the way out.
        """
        direct = self.storage_driver.local_path(key)
        if direct is not None:
            yield direct
            return

        data = self.storage_driver.get_bytes(key)
        with tempfile.NamedTemporaryFile(suffix=suffix or "", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            yield tmp_path
        finally:
            tmp_path.unlink(missing_ok=True)

    # ========== Upload ==========

    async def upload_media(
        self,
        file_data: bytes,
        filename: str,
        content_type: Optional[str],
        user_id: Optional[str] = None,
        purpose: str = UPLOAD_PURPOSE_USER
    ) -> UploadResult:
        """Upload a media file.

        Args:
            file_data: File content as bytes
            filename: Original filename
            content_type: MIME type of the file
            user_id: User ID
            purpose: Why this upload exists - 'user_upload' (default, a real
                Library resource) or 'derived_artifact' (a file some other
                feature needed addressable by path, e.g. an inpainting mask;
                stays servable but never appears in the Library)

        Returns:
            UploadResult with file info

        Raises:
            ValueError: If upload fails, is blocked, or `purpose` is unknown
        """
        purpose = validate_upload_purpose_policy(purpose)

        # Validate content type
        if not self.media_types.is_valid_media_type(content_type):
            raise ValueError("Only image, video, and audio files are allowed")

        # Execute before_upload hook
        hook_data, blocked = execute_hook(self.plugins,
            MEDIA_HOOKS.before_upload,
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(file_data),
                "user_id": user_id
            }
        )

        if blocked:
            raise ValueError(hook_data.get("block_reason", "Upload blocked"))

        # Generate unique filename and store it through the configured
        # backend (local disk by default, optionally S3 - see
        # `StorageSettings`).
        file_ext = Path(filename).suffix if filename else ".png"
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        key = self._upload_key(unique_filename)

        self.storage_driver.put_bytes(key, file_data)
        relative_path = key
        stored_path = self.storage_driver.local_path(key)
        display_path = str(stored_path) if stored_path is not None else key

        # Execute after_upload hook
        execute_hook(self.plugins,
            MEDIA_HOOKS.after_upload,
            {
                "filename": unique_filename,
                "path": display_path,
                "relative_path": relative_path,
                "size": len(file_data),
                "user_id": user_id
            }
        )

        logger.info(f"Uploaded media file: {unique_filename}")

        with self._local_copy(key, file_ext) as local_path:
            width, height, duration_seconds, fps = self._probe_upload_metadata(local_path)

        # A real Library resource gets the same thumbnail treatment a
        # generation output does - images synchronously (cheap, in-process),
        # video asynchronously (ffmpeg is slow enough to stall the response).
        # `derived_artifact` uploads (masks, ...) never appear in the
        # Library, so there is nothing to render a tile for.
        thumbnail_paths: Dict[str, str] = {}
        stem = Path(unique_filename).stem
        if purpose == UPLOAD_PURPOSE_USER and self.media_types.is_image(file_ext):
            thumbnail_paths = self._generate_upload_image_thumbnails(file_data, stem)

        created_upload = self._record_upload_ownership(
            user_id=user_id,
            filename=unique_filename,
            original_filename=filename,
            content_type=content_type,
            file_size=len(file_data),
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=fps,
            purpose=purpose,
            thumbnail_paths=thumbnail_paths,
        )

        if purpose == UPLOAD_PURPOSE_USER and created_upload and self.media_types.is_video(file_ext):
            self._schedule_upload_video_thumbnails(key, file_ext, created_upload.id, stem)

        return UploadResult(
            path=display_path,
            relative_path=relative_path,
            filename=unique_filename,
            size=len(file_data),
            url=f"/api/media/uploads/{unique_filename}",
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            fps=fps
        )

    def _generate_upload_image_thumbnails(self, file_data: bytes, stem: str) -> Dict[str, str]:
        """The same three-size thumbnail set `ImageGenerationOutputHandler`
        generates for a saved generation output, reused rather than
        reimplemented. Best-effort: a decode failure leaves the upload
        thumbnail-less, exactly like a failed metadata probe leaves it
        dimension-less - neither fails the upload itself."""
        try:
            image = Image.open(io.BytesIO(file_data))
            image.load()
            return generate_thumbnails(image, self.storage_driver, "uploads", stem)
        except Exception as e:
            logger.warning(f"Failed to generate thumbnails for uploaded image: {e}")
            return {}

    def _schedule_upload_video_thumbnails(self, key: str, file_ext: str, upload_id: str, stem: str) -> None:
        """Generate a video upload's thumbnails off the request thread, the
        same way `VideoGenerationOutputHandler` does for generation output.

        Unlike that handler this needs no wait/ffprobe-readiness loop first:
        `key`'s bytes were already written by a completed `put_bytes` call
        before this is scheduled, not streamed in over time.
        """
        storage_driver = self.storage_driver
        upload_repo = self.upload_repo

        def worker():
            try:
                with self._local_copy(key, file_ext) as local_path:
                    thumbnail_paths = generate_video_thumbnails(str(local_path), storage_driver, "uploads", stem)

                if thumbnail_paths:
                    upload_repo.set_thumbnail_paths(
                        upload_id,
                        thumbnail_paths.get('small'),
                        thumbnail_paths.get('medium'),
                        thumbnail_paths.get('large'),
                    )
                else:
                    logger.warning(f"Failed to generate thumbnails for uploaded video: {key}")
            except Exception as e:
                logger.error(f"Error generating thumbnails for uploaded video {key}: {e}")

        threading.Thread(target=worker, daemon=True).start()

    def _record_upload_ownership(
        self,
        user_id: Optional[str],
        filename: str,
        original_filename: Optional[str],
        content_type: Optional[str],
        file_size: int,
        width: Optional[int],
        height: Optional[int],
        duration_seconds: Optional[float],
        fps: Optional[float],
        purpose: str = UPLOAD_PURPOSE_USER,
        thumbnail_paths: Optional[Dict[str, str]] = None,
    ) -> Optional[Upload]:
        """Record ownership + metadata for a freshly-saved upload.

        Best-effort and non-fatal: an anonymous upload (no `user_id`) has no
        owner to record, and any DB error here must not fail the upload
        itself - the file is already safely on disk and servable either way,
        it just won't show up in anyone's "Load from uploads" library.
        `content_type` is already validated as image/video/audio by this
        point (see the `is_valid_media_type` check earlier in `upload_media`),
        so its prefix is a reliable `media_type` label.

        Returns the created row, so the video thumbnail path can key its
        later async update off its id - or None when there was no owner to
        record or the write failed, both already non-fatal above.
        """
        if not user_id or not content_type:
            return None

        media_type = content_type.split('/', 1)[0]
        thumbnail_paths = thumbnail_paths or {}

        try:
            return self.upload_repo.create(Upload(
                user_id=user_id,
                filename=filename,
                original_filename=original_filename,
                media_type=media_type,
                mime_type=content_type,
                width=width,
                height=height,
                thumbnail_small=thumbnail_paths.get('small'),
                thumbnail_medium=thumbnail_paths.get('medium'),
                thumbnail_large=thumbnail_paths.get('large'),
                duration_seconds=duration_seconds,
                fps=fps,
                file_size=file_size,
                purpose=purpose,
            ))
        except Exception as e:
            logger.warning(f"Failed to record upload ownership for {filename}: {e}")
            return None

    def _probe_upload_metadata(
        self, file_path: Path
    ) -> Tuple[Optional[int], Optional[int], Optional[float], Optional[float]]:
        """Best-effort (width, height, duration_seconds, fps) for a freshly uploaded file.

        Uploads never get a `files` row (see get_uploaded_media), so unlike
        generation output there is nowhere to persist this - it is probed once
        here and handed straight back in the upload response for the caller
        (MediaSelect) to display immediately. Images use the
        same `ImageProcessor` dimension helper the resize/thumbnail paths use;
        videos reuse the ffprobe/cv2 probe shared with the video generation
        output handler; audio reuses the soundfile-backed probe shared with
        the audio generation output handler. `duration_seconds`/`fps` are
        always None for images, and `width`/`height`/`fps` are always None
        for audio.
        """
        suffix = file_path.suffix
        try:
            if self.media_types.is_image(suffix):
                width, height = self.image_processor.get_image_dimensions(file_path)
                return width, height, None, None
            if self.media_types.is_video(suffix):
                width, height = media_probe.get_video_dimensions(str(file_path))
                duration_seconds, fps = media_probe.get_video_duration_fps(str(file_path))
                return width, height, duration_seconds, fps
            if self.media_types.is_audio(suffix):
                duration_seconds = media_probe.get_audio_duration_seconds(str(file_path))
                return None, None, duration_seconds, None
        except Exception as e:
            logger.warning(f"Failed to probe metadata for upload {file_path.name}: {str(e)}")

        return None, None, None, None

    # ========== Upload Library ==========

    def list_uploads(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> UploadListResult:
        """List the current user's media-loader uploads, newest first.

        Args:
            user_id: Owning user - the only scope this ever queries
            media_type: Optional filter ('image' | 'video' | 'audio')
            limit: Page size, clamped to `MAX_UPLOAD_LIST_LIMIT`
            offset: Page offset

        Returns:
            UploadListResult with this page plus the total matching count
        """
        limit = max(1, min(limit, MAX_UPLOAD_LIST_LIMIT))
        offset = max(0, offset)

        uploads = self.upload_repo.list_for_user(
            user_id, media_type=media_type, limit=limit, offset=offset
        )
        total = self.upload_repo.count_for_user(user_id, media_type=media_type)

        return UploadListResult(
            uploads=[
                UploadFileInfo(
                    id=u.id,
                    filename=u.filename,
                    original_filename=u.original_filename,
                    media_type=u.media_type,
                    mime_type=u.mime_type,
                    url=f"/api/media/uploads/{u.filename}",
                    thumbnail_small=f"/api/media/uploads/{u.filename}?size=small" if u.thumbnail_small else None,
                    thumbnail_medium=f"/api/media/uploads/{u.filename}?size=medium" if u.thumbnail_medium else None,
                    thumbnail_large=f"/api/media/uploads/{u.filename}?size=large" if u.thumbnail_large else None,
                    width=u.width,
                    height=u.height,
                    duration_seconds=u.duration_seconds,
                    fps=u.fps,
                    size=u.file_size,
                    created_at=u.created_at.isoformat() if u.created_at else None,
                )
                for u in uploads
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    def delete_upload(self, filename: str, user_id: str) -> None:
        """Delete one of the current user's uploads - DB row and file on disk.

        Args:
            filename: The upload's on-disk filename
            user_id: The requesting user - must be the owner

        Raises:
            ValueError: If no upload with this filename is owned by this
                user. The controller maps this to a uniform 404 - never a
                403 - so probing another user's filename can't be
                distinguished from a filename that doesn't exist at all
                (GenerationPolicy precedent).
        """
        record = self.upload_repo.get_by_filename(filename, user_id)
        if not record:
            raise ValueError("Upload not found")

        # Same containment-checked key the serve/info routes use.
        self.storage_driver.delete(self._upload_key(filename))

        self.upload_repo.delete(filename, user_id)

    # ========== List/Delete ==========

    def list_generation_media(
        self,
        generation_id: str,
        user_id: str
    ) -> MediaListResult:
        """List all media files for a generation.

        Args:
            generation_id: Generation identifier
            user_id: User ID for access control

        Returns:
            MediaListResult with file info

        Raises:
            ValueError: If generation not found
        """
        generation = self.generation_repo.get_by_id(generation_id, user_id=user_id)
        if not generation:
            raise ValueError("Generation not found or access denied")

        files = self.generation_repo.get_files(generation_id, is_final=True)

        media_files = []
        for file in files:
            filename = Path(file.file_path).name
            media_files.append(MediaFileInfo(
                id=file.id,
                filename=filename,
                file_type=file.file_type,
                mime_type=file.mime_type,
                size=file.file_size,
                url=f"/api/media/generations/{generation_id}/{filename}",
                thumbnail_small=f"/api/media/generations/{generation_id}/{filename}?size=small" if file.thumbnail_small else None,
                thumbnail_medium=f"/api/media/generations/{generation_id}/{filename}?size=medium" if file.thumbnail_medium else None,
                thumbnail_large=f"/api/media/generations/{generation_id}/{filename}?size=large" if file.thumbnail_large else None,
                width=file.width,
                height=file.height,
                duration_seconds=file.duration_seconds,
                fps=file.fps,
            ))

        return MediaListResult(
            generation_id=generation_id,
            media_count=len(media_files),
            media=media_files
        )

    def delete_generation_media(
        self,
        generation_id: str,
        user_id: str
    ) -> DeleteResult:
        """Delete all media files for a generation.

        Args:
            generation_id: Generation identifier
            user_id: User ID for access control

        Returns:
            DeleteResult with deletion info

        Raises:
            ValueError: If generation not found or delete blocked
        """
        generation = self.generation_repo.get_by_id(generation_id, user_id=user_id)
        if not generation:
            raise ValueError("Generation not found or access denied")

        # Execute before_delete hook
        hook_data, blocked = execute_hook(self.plugins,
            MEDIA_HOOKS.before_delete,
            {
                "generation_id": generation_id,
                "user_id": user_id
            }
        )

        if blocked:
            raise ValueError(hook_data.get("block_reason", "Delete blocked"))

        # `delete_generation_outputs` has no directory to scan - it deletes
        # exactly the keys it's given, so every file plus its thumbnails
        # (not tracked as their own `files` rows) has to be enumerated here.
        relative_paths = []
        for file_record in self.generation_repo.get_files(generation_id):
            relative_paths.append(file_record.file_path)
            base_key = Path(file_record.file_path).parent
            for thumbnail in (file_record.thumbnail_small, file_record.thumbnail_medium, file_record.thumbnail_large):
                if thumbnail:
                    relative_paths.append((base_key / thumbnail).as_posix())

        deleted, failed = self.file_service.delete_generation_outputs(relative_paths)

        # Delete from database
        self.generation_repo.delete(generation_id)

        # Execute after_delete hook
        execute_hook(self.plugins,
            MEDIA_HOOKS.after_delete,
            {
                "generation_id": generation_id,
                "deleted_files": deleted,
                "failed_files": failed,
                "user_id": user_id
            }
        )

        logger.info(f"Deleted generation media: {generation_id}")

        return DeleteResult(
            generation_id=generation_id,
            deleted_files=deleted,
            failed_files=failed
        )

    # ========== File by ID ==========

    def get_file_by_id(
        self,
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size: Optional[str] = None
    ) -> MediaResult:
        """Get a file by its database ID.

        Args:
            file_id: File database ID
            width: Optional resize width
            height: Optional resize height
            size: Optional thumbnail size

        Returns:
            MediaResult with file info

        Raises:
            ValueError: If file not found
        """
        logger.debug(f"Serving file with ID: {file_id}")

        # Get file from database
        file_record = self.file_repo.get_by_id(file_id)
        if not file_record:
            logger.warning(f"File ID {file_id} not found in database")
            raise ValueError("File not found")

        # Get user_id from file or related generation
        user_id = getattr(file_record, 'user_id', None)
        if not user_id:
            # Try to get from generation
            gen_file_record = self.file_repo.get_generation_file_by_file_id(file_id)
            if gen_file_record:
                generation = self.generation_repo.get_by_id(gen_file_record.generation_id)
                if generation:
                    user_id = generation.user_id

        # Resolve which key to serve - the original, or a thumbnail beside it
        key = file_record.file_path
        if size and size in ['small', 'medium', 'large']:
            thumbnail_path = self.file_resolver.get_thumbnail_path(file_record, size)
            if thumbnail_path:
                base_key = Path(file_record.file_path).parent
                thumbnail_key = (base_key / thumbnail_path).as_posix()
                if self.storage_driver.exists(thumbnail_key):
                    key = thumbnail_key
                    logger.debug(f"Serving {size} thumbnail: {thumbnail_key}")

        if not self.storage_driver.exists(key):
            logger.error(f"File not found on disk: {key}")
            raise ValueError(f"File not found on disk: {key}")

        media_type = self.media_types.get_media_type(Path(key).suffix)

        # Handle image resizing if requested
        if (width or height) and self.media_types.is_resizable(Path(key).suffix):
            try:
                with self._local_copy(key, Path(key).suffix) as local_path:
                    content = self.image_processor.resize_image(local_path, width, height)
                return MediaResult(
                    content=content,
                    media_type=media_type,
                    headers={"Cache-Control": "public, max-age=3600"},
                    use_streaming=False
                )
            except Exception as e:
                logger.warning(f"Failed to resize image: {str(e)}")

        # Return original file
        content = self.storage_driver.get_bytes(key)

        return MediaResult(
            content=content,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"},
            use_streaming=False
        )

    def get_file_blob(
        self,
        file_id: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        user_id: Optional[str] = None
    ) -> MediaResult:
        """Get a file as blob data.

        This is similar to get_file_by_id but intended for frontend blob URL creation.

        Args:
            file_id: File database ID
            width: Optional resize width
            height: Optional resize height
            user_id: Optional user ID (for future access control)

        Returns:
            MediaResult with file content

        Raises:
            ValueError: If file not found
        """
        # Delegate to get_file_by_id - same logic
        return self.get_file_by_id(file_id, width, height)

    # ========== Preset Files ==========

    def _preset_thumbnail_cache_path(
        self, preset_id: str, file_path: str, size: str, mtime_ns: int, suffix: str
    ) -> Path:
        """Cache path for one rendered preset thumbnail.

        Lives under the storage dir, never inside the preset tree, which is
        git-tracked and may be read-only. The source mtime is part of the name, so
        editing an image simply misses the cache instead of serving a stale render.
        """
        digest = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:16]
        cache_dir = Path(self.file_resolver.get_storage_directory()) / "preset_media" / preset_id
        return cache_dir / f"{digest}_{size}_{mtime_ns}{suffix}"

    def purge_preset_thumbnail_cache(self, preset_id: str) -> None:
        """Drop every rendered thumbnail for a preset. Called on preset reload."""
        cache_dir = Path(self.file_resolver.get_storage_directory()) / "preset_media" / preset_id
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
            logger.debug(f"Purged preset thumbnail cache for {preset_id}")

    def get_preset_file(
        self,
        preset_id: str,
        file_path: str,
        size: Optional[str] = None
    ) -> MediaResult:
        """Get a servable asset from a preset directory.

        Args:
            preset_id: Preset identifier
            file_path: Relative path within preset directory
            size: Optional render size - one of PRESET_THUMBNAIL_WIDTHS

        Returns:
            MediaResult with file info

        Raises:
            ValueError: If preset or file not found, or the file is not servable
            UnsupportedSizeError: If `size` is not a recognised size name
        """
        if size is not None and size not in PRESET_THUMBNAIL_WIDTHS:
            raise UnsupportedSizeError(
                f"Unknown size '{size}'. Must be one of: {sorted(PRESET_THUMBNAIL_WIDTHS)}"
            )

        full_file_path = self.file_resolver.resolve_preset_file(preset_id, file_path)

        if not full_file_path.exists():
            raise ValueError("Preset file not found")

        media_type = self.media_types.get_media_type(full_file_path.suffix)
        mtime_ns = full_file_path.stat().st_mtime_ns

        # Videos and .gif are not resizable; they fall through and stream as-is.
        if size and self.media_types.is_resizable(full_file_path.suffix):
            cache_path = self._preset_thumbnail_cache_path(
                preset_id, file_path, size, mtime_ns, full_file_path.suffix
            )
            try:
                if not cache_path.exists():
                    content = self.image_processor.generate_thumbnail(
                        full_file_path, width=PRESET_THUMBNAIL_WIDTHS[size]
                    )
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    # Write-then-rename so a concurrent reader never sees a partial file.
                    tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
                    tmp_path.write_bytes(content)
                    tmp_path.replace(cache_path)
                    self._prune_stale_thumbnails(cache_path)

                return MediaResult(
                    file_path=str(cache_path),
                    media_type=media_type,
                    headers=self._preset_headers(preset_id, file_path, size, mtime_ns),
                    use_streaming=True
                )
            except Exception as e:
                logger.warning(f"Failed to generate thumbnail: {str(e)}, serving original")

        headers = self._preset_headers(preset_id, file_path, None, mtime_ns)
        headers.update({
            "Accept-Ranges": "bytes",
            "Content-Length": str(full_file_path.stat().st_size),
            "Content-Disposition": f'inline; filename="{full_file_path.name}"',
        })

        return MediaResult(
            file_path=str(full_file_path),
            media_type=media_type,
            headers=headers,
            use_streaming=True
        )

    @staticmethod
    def _preset_headers(
        preset_id: str, file_path: str, size: Optional[str], mtime_ns: int
    ) -> dict:
        """Cache headers whose ETag changes when the source image changes."""
        variant = f"-{size}" if size else ""
        return {
            "Cache-Control": "public, max-age=3600",
            "ETag": f'"{preset_id}-{file_path}{variant}-{mtime_ns}"',
        }

    @staticmethod
    def _prune_stale_thumbnails(current: Path) -> None:
        """Remove renders of earlier versions of the same source+size."""
        prefix = current.name.rsplit("_", 1)[0]  # strip the `_<mtime><suffix>` tail
        for sibling in current.parent.glob(f"{prefix}_*"):
            if sibling != current:
                sibling.unlink(missing_ok=True)

    # ========== File Parameters ==========

    def get_file_params(
        self,
        file_id: str,
        user_id: str
    ) -> FileParamsResult:
        """Get parameters associated with a generated file.

        Args:
            file_id: File database ID
            user_id: User ID for access control

        Returns:
            FileParamsResult with file and generation parameters

        Raises:
            ValueError: If file or generation not found
        """
        from src.features.generation.parameter_repository import generation_parameter_repo

        # Verify user owns this file
        file = self.file_repo.get_by_id(file_id, user_id=user_id)
        if not file:
            raise ValueError("File not found or access denied")

        # Get generation file record to find generation_id
        gen_file_record = self.file_repo.get_generation_file_by_file_id(file_id)
        if not gen_file_record:
            raise ValueError("File is not associated with a generation")

        # Get the generation
        generation = self.generation_repo.get_by_id(gen_file_record.generation_id, user_id=user_id)
        if not generation:
            raise ValueError("Generation not found or access denied")

        # Get generation parameters
        params = generation_parameter_repo.get_by_generation_id(gen_file_record.generation_id)

        return FileParamsResult(
            file_id=file_id,
            generation_id=gen_file_record.generation_id,
            file_path=file.file_path,
            file_type=file.file_type,
            mime_type=file.mime_type,
            created_at=file.created_at.isoformat() if file.created_at else None,
            generation={
                "preset_id": generation.preset_id,
                "preset_version": generation.preset_version,
                "form_data": generation.form_data,
                "status": generation.status,
                "created_at": generation.created_at.isoformat() if generation.created_at else None,
                "completed_at": generation.completed_at.isoformat() if generation.completed_at else None
            },
            parameters=[
                {
                    "key": param.parameter_key,
                    "value": param.parameter_value,
                    "type": param.value_type
                }
                for param in params
            ]
        )
