"""
Media editing manager.

Edits a library resource - an `uploads` row plus its bytes - and either replaces
it in place or saves the result alongside it. The transform itself lives in
`operations.py`; what this owns is everything around it: proving the resource is
the caller's, resolving its storage key, staging it locally for tools that need
a real file, running the encode off the event loop, and leaving the storage
driver and the `uploads` table agreeing with each other whichever step fails.

Two failure vocabularies, deliberately: a plain `ValueError` means "no such
resource" and the controller answers 404 for it whether the row is missing or
belongs to someone else (the `delete_upload` precedent in
`src.features.media.routes`), while `InvalidEditError` means the caller asked
for something this media cannot do and answers 400.
"""

import asyncio
import logging
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Optional, Sequence, TYPE_CHECKING

from src.features.media.editing.dto import (
    EditedMediaItem,
    EditMediaResult,
    EditOperation,
)
from src.features.media.editing.operations import (
    EditedMediaMetadata,
    InvalidEditError,
    apply_audio_operations,
    apply_image_operations,
    apply_video_operations,
    extract_video_frame,
    split_audio,
)
from src.features.media.records import Upload
from src.platform.filesystem.storage_driver import StorageKeyError, local_copy, uploads_key

if TYPE_CHECKING:
    from src.features.media.media_types import MediaTypeResolver
    from src.features.media.upload_repository import UploadRepository
    from src.platform.filesystem.storage_driver import FileStorageDriver

logger = logging.getLogger(__name__)

EDITABLE_MEDIA_TYPES = ("image", "video", "audio")

_FRAME_SUFFIX = ".png"


class MediaEditManager:
    """Applies edits to a user's library resources."""

    def __init__(
        self,
        upload_repository: "UploadRepository",
        media_type_resolver: "MediaTypeResolver",
        storage_driver: "FileStorageDriver",
    ):
        self.upload_repo = upload_repository
        self.media_types = media_type_resolver
        self.storage_driver = storage_driver

    async def edit_item(
        self,
        item_id: str,
        user_id: str,
        operations: Sequence[EditOperation],
        mode: str = "new",
    ) -> EditMediaResult:
        """Apply `operations` to one of the user's library resources.

        Args:
            item_id: The `uploads` row id to edit
            user_id: The requesting user - must be the owner
            operations: Ordered operations, each checked against what the one
                before it left behind
            mode: 'new' to save alongside the original, 'replace' to swap the
                file behind the same row (keeping its tags and collections)

        Returns:
            EditMediaResult holding the resulting resource.

        Raises:
            ValueError: If no such resource is owned by this user.
            InvalidEditError: If the operations cannot be applied to it.
            MediaEditFailedError: If the encoder failed or is unavailable.
        """
        if mode not in ("new", "replace"):
            raise InvalidEditError(f"Unknown mode: {mode}")

        upload, source_key = self._resolve_owned_source(item_id, user_id)
        self._require_editable(upload.media_type)

        suffix = Path(upload.filename).suffix
        transform = {
            "image": apply_image_operations,
            "video": apply_video_operations,
            "audio": apply_audio_operations,
        }[upload.media_type]

        dest_key, metadata = await self._transform_and_publish(
            source_key, suffix, transform, list(operations)
        )

        if mode == "replace":
            return EditMediaResult(
                item=self._replace(upload, user_id, source_key, dest_key, metadata),
                replaced=True,
            )
        return EditMediaResult(
            item=self._save_as_new(upload, user_id, dest_key, metadata, upload.media_type),
            replaced=False,
        )

    async def extract_frame(
        self,
        item_id: str,
        user_id: str,
        time_seconds: float,
    ) -> EditMediaResult:
        """Lift one frame of the user's video out as a new image resource.

        Always a new resource: replacing a video row with a still would leave
        every reference to it - a form field, a collection - pointing at a
        different medium than the one it was given.

        Raises:
            ValueError: If no such resource is owned by this user.
            InvalidEditError: If the resource is not a video, or the time is
                outside it.
            MediaEditFailedError: If the encoder failed or is unavailable.
        """
        upload, source_key = self._resolve_owned_source(item_id, user_id)
        if upload.media_type != "video":
            raise InvalidEditError(
                f"Only a video has frames to extract, not {upload.media_type}"
            )

        dest_key, metadata = await self._transform_and_publish(
            source_key, _FRAME_SUFFIX, extract_video_frame, time_seconds
        )

        stem = Path(upload.original_filename or upload.filename).stem
        return EditMediaResult(
            item=self._save_as_new(
                upload,
                user_id,
                dest_key,
                metadata,
                "image",
                original_filename=f"{stem}-frame{_FRAME_SUFFIX}",
            ),
            replaced=False,
        )

    async def split_item(
        self,
        item_id: str,
        user_id: str,
        part_seconds: float,
    ) -> List[EditedMediaItem]:
        """Split one of the user's audio resources into fixed-length parts.

        The original is never touched - every part is a new resource. If a
        part fails to persist, every part already published for this split is
        rolled back (row and file alike) rather than left as a partial split.

        Raises:
            ValueError: If no such resource is owned by this user.
            InvalidEditError: If the resource is not audio, or the part length
                cannot be applied to it.
            MediaEditFailedError: If the encoder failed or is unavailable.
        """
        upload, source_key = self._resolve_owned_source(item_id, user_id)
        if upload.media_type != "audio":
            raise InvalidEditError(f"Only audio can be split into parts, not {upload.media_type}")

        suffix = Path(upload.filename).suffix
        stem = Path(upload.original_filename or upload.filename).stem

        with local_copy(self.storage_driver, source_key, suffix) as source_path, \
                self._scratch_dir() as dest_dir:
            parts = await asyncio.to_thread(split_audio, source_path, dest_dir, suffix, part_seconds)
            total = len(parts)

            items: List[EditedMediaItem] = []
            published_keys: List[str] = []
            try:
                for index, (part_path, metadata) in enumerate(parts, start=1):
                    dest_key = self._new_destination_key(suffix)
                    self.storage_driver.put_file(dest_key, part_path)
                    published_keys.append(dest_key)
                    items.append(self._save_as_new(
                        upload, user_id, dest_key, metadata, "audio",
                        original_filename=f"{stem} — part {index}/{total}{suffix}",
                    ))
            except Exception:
                for created in items:
                    self.upload_repo.delete(created.filename, user_id)
                for key in published_keys:
                    self._remove(key)
                raise

        logger.info(f"Split {upload.id} into {total} new library resources")
        return items

    # ========== Internals ==========

    def _resolve_owned_source(self, item_id: str, user_id: str):
        """The caller's resource and its storage key, or the uniform not-found error."""
        upload = self.upload_repo.get_by_id(item_id, user_id)
        if not upload:
            raise ValueError("Library item not found")

        # Same containment-checked key the serve and delete paths use.
        key = self._upload_key(upload.filename)
        if not self.storage_driver.exists(key):
            # The row outlived its file. Reported as a missing resource rather
            # than a server error - there is nothing here to edit either way.
            raise ValueError("Library item not found")

        return upload, key

    def _require_editable(self, media_type: str) -> None:
        if media_type not in EDITABLE_MEDIA_TYPES:
            raise InvalidEditError(f"Cannot edit a {media_type} resource")

    def _upload_key(self, filename: str) -> str:
        try:
            return uploads_key(filename)
        except StorageKeyError:
            raise ValueError("Access denied - path traversal detected")

    async def _transform_and_publish(
        self,
        source_key: str,
        suffix: str,
        transform_fn: Callable[[Path, Path, object], EditedMediaMetadata],
        extra_arg: object,
    ) -> tuple[str, EditedMediaMetadata]:
        """Stage `source_key` to a local file, run `transform_fn` off the event
        loop against a scratch destination, and publish the result through the
        driver.

        The transform tools (Pillow, ffmpeg) need real filesystem paths on both
        ends regardless of storage backend - `local_copy` provides one for the
        source; nothing is published if the transform raises.
        """
        with local_copy(self.storage_driver, source_key, suffix) as source_path, \
                self._scratch_path(suffix) as dest_path:
            metadata = await asyncio.to_thread(transform_fn, source_path, dest_path, extra_arg)
            dest_key = self._new_destination_key(suffix)
            self.storage_driver.put_file(dest_key, dest_path)
        return dest_key, metadata

    @contextmanager
    def _scratch_path(self, suffix: str):
        """A fresh local path for a transform's output, before it is published.

        The transform tools write to a path that does not exist yet - same
        contract `NamedTemporaryFile` breaks, hence a plain generated name.
        Removed whether or not publishing happens.
        """
        path = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}{suffix}"
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    @contextmanager
    def _scratch_dir(self):
        """A fresh local directory for a batch transform's output parts.

        Removed whether or not every part was published, so a rollback never
        leaves scratch parts behind alongside the published ones it also cleans.
        """
        path = Path(tempfile.gettempdir()) / f"split-{uuid.uuid4()}"
        path.mkdir(parents=True, exist_ok=True)
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def _new_destination_key(self, suffix: str) -> str:
        """A fresh, containment-checked storage key in the uploads namespace.

        Same uuid-name convention as `MediaManager.upload_media`, so an edited
        resource is indistinguishable from an uploaded one by its name - and a
        replace lands on a new URL, which is what stops a browser serving the
        pre-edit bytes out of its cache.
        """
        return self._upload_key(f"{uuid.uuid4()}{suffix.lower()}")

    def _save_as_new(
        self,
        source_upload: Upload,
        user_id: str,
        dest_key: str,
        metadata: EditedMediaMetadata,
        media_type: str,
        original_filename: Optional[str] = None,
    ) -> EditedMediaItem:
        """Record the published file as a second, independent resource."""
        try:
            created = self.upload_repo.create(Upload(
                user_id=user_id,
                filename=Path(dest_key).name,
                original_filename=original_filename or source_upload.original_filename,
                media_type=media_type,
                mime_type=self.media_types.get_media_type(Path(dest_key).suffix),
                width=metadata.width,
                height=metadata.height,
                duration_seconds=metadata.duration_seconds,
                fps=metadata.fps,
                file_size=self.storage_driver.size(dest_key),
            ))
        except Exception:
            # The bytes are stored but nothing owns them; an unreferenced file
            # in the uploads namespace is served to anyone who guesses its name.
            self._remove(dest_key)
            raise

        logger.info(f"Edited {source_upload.id} into new library resource {created.id}")
        return self._to_item(created)

    def _replace(
        self,
        upload: Upload,
        user_id: str,
        source_key: str,
        dest_key: str,
        metadata: EditedMediaMetadata,
    ) -> EditedMediaItem:
        """Point the existing row at the published file, then drop the old one.

        The row keeps its id, so the `upload_tags` and `collection_uploads` rows
        that reference it survive the edit untouched.
        """
        try:
            updated = self.upload_repo.update_file(
                upload_id=upload.id,
                user_id=user_id,
                filename=Path(dest_key).name,
                mime_type=self.media_types.get_media_type(Path(dest_key).suffix),
                width=metadata.width,
                height=metadata.height,
                duration_seconds=metadata.duration_seconds,
                fps=metadata.fps,
                file_size=self.storage_driver.size(dest_key),
            )
        except Exception:
            # The row still points at the original, which is still stored.
            self._remove(dest_key)
            raise

        if not updated:
            self._remove(dest_key)
            raise ValueError("Library item not found")

        # Only now is the original unreferenced. Failing here costs a stray
        # file, not the edit.
        self._remove(source_key)

        logger.info(f"Replaced library resource {upload.id} with an edited file")
        return self._to_item(updated)

    def _remove(self, key: str) -> None:
        try:
            self.storage_driver.delete(key)
        except Exception as e:
            logger.warning(f"Could not remove {key} after an edit: {e}")

    def _to_item(self, upload: Upload) -> EditedMediaItem:
        return EditedMediaItem(
            id=upload.id,
            filename=upload.filename,
            original_filename=upload.original_filename,
            media_type=upload.media_type,
            mime_type=upload.mime_type,
            url=f"/api/media/uploads/{upload.filename}",
            width=upload.width,
            height=upload.height,
            duration_seconds=upload.duration_seconds,
            fps=upload.fps,
            size=upload.file_size,
            created_at=upload.created_at.isoformat() if upload.created_at else None,
        )
