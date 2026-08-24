"""
Library domain manager.

The library is a user's private collection of media resources: files they
uploaded, plus copies they took of their own generation output. Everything it
holds is a row in `uploads` (migration 087) - the same table, and the same
`/api/media/uploads/{filename}` serving route, that the media-loader upload
flow writes.

Framework-agnostic: raises ValueError, which the controller maps to a uniform
404. Every method takes the requesting user and filters on it; an item that is
not yours is reported exactly like an item that does not exist, so a probe
cannot confirm another user's library holds a given file (the `delete_upload`
precedent in `src.features.media.routes`).
"""

import logging
import mimetypes
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.features.library.dto import (
    LibraryFacets,
    LibraryItem,
    LibraryListResult,
)
from src.features.library.repository import LibraryRepository
from src.features.media.records import Upload
from src.features.media.upload_repository import UploadRepository
from src.features.tags.dto import TagType
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key

if TYPE_CHECKING:
    from src.features.generation.file_repository import FileRepository
    from src.features.generation.records import File
    from src.features.media.file_resolver import FilePathResolver
    from src.features.tags.repository import TagRepository
    from src.platform.filesystem import FileStore
    from src.platform.filesystem.storage_driver import FileStorageDriver

logger = logging.getLogger(__name__)

MAX_LIBRARY_LIST_LIMIT = 200

# The media kinds a library resource can be. Mirrors the media-loader upload
# gate (`MediaTypeResolver.is_valid_media_type`) - a mesh or anything else a
# pipe can emit has no place in a field a user picks an image or clip from.
LIBRARY_MEDIA_TYPES = ("image", "video", "audio")

# Generation `files.file_type` -> the library's `media_type` vocabulary.
_FILE_TYPE_TO_MEDIA_TYPE = {
    "IMAGE": "image",
    "VIDEO": "video",
    "AUDIO": "audio",
}


class LibraryManager:
    """Coordinates library reads, curation and history -> library copies."""

    def __init__(
        self,
        library_repository: LibraryRepository,
        upload_repository: UploadRepository,
        tag_repository: "TagRepository",
        file_repository: "FileRepository",
        file_resolver: "FilePathResolver",
        file_store: "FileStore",
        storage_driver: "FileStorageDriver",
    ):
        self.repository = library_repository
        self.upload_repo = upload_repository
        self.tag_repo = tag_repository
        self.file_repo = file_repository
        self.file_resolver = file_resolver
        self.file_store = file_store
        self.storage_driver = storage_driver

    # ========== Read ==========

    def list_items(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> LibraryListResult:
        """One filtered page of the user's library.

        Costs a constant three queries - page, count, one batched tag fetch -
        however many rows the page holds.

        Raises:
            ValueError: If a filter is not valid for this user (unknown media
                type, or a tag that is not an UPLOAD tag they own).
        """
        if media_type and media_type not in LIBRARY_MEDIA_TYPES:
            raise ValueError(f"Invalid media type: {media_type}")

        tag_ids = [t for t in (tag_ids or []) if t]
        if tag_ids:
            self._validate_tag_ids(tag_ids, user_id)

        limit = max(1, min(limit, MAX_LIBRARY_LIST_LIMIT))
        offset = max(0, offset)

        filters = dict(
            media_type=media_type,
            tag_ids=tag_ids or None,
            collection_id=collection_id,
            search=(search or "").strip() or None,
        )

        uploads = self.repository.list_items(user_id, limit=limit, offset=offset, **filters)
        total = self.repository.count_items(user_id, **filters)
        tags_by_id = self.tag_repo.get_upload_tags_bulk([u.id for u in uploads])

        return LibraryListResult(
            items=[self._to_item(u, tags_by_id.get(u.id, [])) for u in uploads],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_facets(self, user_id: str) -> LibraryFacets:
        """Per-media-type counts for the user's whole library."""
        return LibraryFacets(media_types=self.repository.media_type_counts(user_id))

    def get_item(self, item_id: str, user_id: str) -> LibraryItem:
        """One library item owned by the user.

        Raises:
            ValueError: If no such item is owned by this user.
        """
        upload = self._get_owned_or_raise(item_id, user_id)
        return self._to_item(upload, self.tag_repo.get_upload_tags(upload.id))

    # ========== Curation ==========

    def get_tags(self, item_id: str, user_id: str) -> List[Dict[str, Any]]:
        """Tags on one library item owned by the user."""
        self._get_owned_or_raise(item_id, user_id)
        return [tag.model_dump() for tag in self.tag_repo.get_upload_tags(item_id)]

    def set_tags(self, item_id: str, tag_ids: List[str], user_id: str) -> List[Dict[str, Any]]:
        """Replace a library item's tags.

        Raises:
            ValueError: If the item is not owned by this user, or a tag is not
                an UPLOAD tag they own.
        """
        self._get_owned_or_raise(item_id, user_id)
        tag_ids = [t for t in (tag_ids or []) if t]
        self._validate_tag_ids(tag_ids, user_id)

        self.tag_repo.set_upload_tags(item_id, tag_ids)
        return [tag.model_dump() for tag in self.tag_repo.get_upload_tags(item_id)]

    # ========== Write ==========

    def delete_item(self, item_id: str, user_id: str) -> None:
        """Delete one library item - DB row and file on disk.

        Tag and collection memberships go with the row (both junctions cascade
        from `uploads`, migration 115).

        Raises:
            ValueError: If no such item is owned by this user.
        """
        upload = self._get_owned_or_raise(item_id, user_id)

        # Same containment-checked key the serve route uses.
        self.storage_driver.delete(self._upload_key(upload.filename))

        self.upload_repo.delete(upload.filename, user_id)

    def copy_generation_file(self, file_id: str, user_id: str) -> LibraryItem:
        """Copy one of the user's generated files into their library.

        The result is a resource, not a reference: fresh bytes under a fresh
        name in `storage/uploads/`, and an `uploads` row that carries only what
        a direct upload of the same file would carry - owner, name, media type,
        dimensions. No preset, no prompt, no seed, no generation id; the
        `uploads` table has nowhere to put them, which is what makes the copy
        indistinguishable from an upload rather than merely undecorated.

        Deleting the generation afterwards leaves this untouched - the bytes
        were copied, and no foreign key points back.

        Raises:
            ValueError: If the file is not found, not owned by this user, or
                not a media type the library holds.
        """
        file_record = self.file_repo.get_by_id(file_id, user_id=user_id)
        if not file_record:
            raise ValueError("File not found")

        media_type = _FILE_TYPE_TO_MEDIA_TYPE.get((file_record.file_type or "").upper())
        if not media_type:
            raise ValueError(f"Cannot add a {file_record.file_type} file to the library")

        source = Path(self.file_store.get_full_path(file_record.file_path))
        storage_root = Path(self.file_store.base_storage_dir)
        if not self.file_resolver.validate_path_security(source, storage_root):
            logger.warning(f"Refused library copy of out-of-tree file path: {file_record.file_path}")
            raise ValueError("File not found")
        if not source.exists():
            raise ValueError("File not found")

        # Same uuid-name convention as `MediaManager.upload_media`, so nothing
        # downstream can tell a copied item from an uploaded one by its name.
        filename = f"{uuid.uuid4()}{source.suffix}"
        key = self._upload_key(filename)
        written = self.storage_driver.put_file(key, source)

        # The source generation file already paid for its thumbnails - copy
        # those bytes alongside it rather than regenerating them.
        thumbnail_paths = self._copy_generation_thumbnails(file_record, source.parent, Path(filename).stem)

        # The source's own basename, never the generation id: a name derived
        # from the generation would be exactly the back-reference this copy is
        # supposed to drop.
        original_filename = source.name
        mime_type = file_record.mime_type or mimetypes.guess_type(source.name)[0]

        upload = self.upload_repo.create(Upload(
            user_id=user_id,
            filename=filename,
            original_filename=original_filename,
            media_type=media_type,
            mime_type=mime_type,
            width=file_record.width,
            height=file_record.height,
            duration_seconds=file_record.duration_seconds,
            fps=file_record.fps,
            file_size=file_record.file_size or written,
            thumbnail_small=thumbnail_paths.get('small'),
            thumbnail_medium=thumbnail_paths.get('medium'),
            thumbnail_large=thumbnail_paths.get('large'),
        ))

        logger.info(f"Copied generated file {file_id} into library as {filename}")
        return self._to_item(upload, [])

    def _copy_generation_thumbnails(
        self, file_record: "File", source_dir: Path, new_stem: str
    ) -> Dict[str, str]:
        """Copy a generation file's thumbnail siblings into the uploads
        namespace, alongside its main copy.

        Best-effort: a missing or out-of-tree sibling just leaves that size
        unset, exactly like an upload whose own thumbnail generation failed -
        it never blocks the copy itself.
        """
        storage_root = Path(self.file_store.base_storage_dir)
        thumbnail_paths: Dict[str, str] = {}

        for size in ("small", "medium", "large"):
            source_name = getattr(file_record, f"thumbnail_{size}", None)
            if not source_name:
                continue

            thumb_source = source_dir / source_name
            if not self.file_resolver.validate_path_security(thumb_source, storage_root) or not thumb_source.exists():
                continue

            relative_path = f"thumbnails/{new_stem}_{size}{thumb_source.suffix}"
            try:
                self.storage_driver.put_file(self._upload_key(relative_path), thumb_source)
                thumbnail_paths[size] = relative_path
            except Exception as e:
                logger.warning(f"Failed to copy {size} thumbnail for file {file_record.id}: {e}")
                continue

            # A video's static thumbnail has an animated sibling next to it
            # (same stem, `_animated.webp`) - carry it too so the copy's
            # hover-preview behaves exactly like the original.
            if (file_record.file_type or "").upper() == "VIDEO":
                animated_source = thumb_source.parent / f"{thumb_source.stem}_animated.webp"
                if animated_source.exists():
                    try:
                        self.storage_driver.put_file(
                            self._upload_key(f"thumbnails/{new_stem}_{size}_animated.webp"), animated_source
                        )
                    except Exception as e:
                        logger.warning(f"Failed to copy animated {size} thumbnail for file {file_record.id}: {e}")

        return thumbnail_paths

    # ========== Internals ==========

    def _upload_key(self, filename: str) -> str:
        """The storage key for an item in the uploads namespace.

        Same containment guarantee `FilePathResolver.resolve_upload_file` gave
        a filesystem path - `validate_key` raises on traversal/absolute input,
        translated to the `ValueError` callers already expect.
        """
        try:
            return uploads_key(filename)
        except StorageKeyError:
            raise ValueError("Access denied - path traversal detected")

    def _get_owned_or_raise(self, item_id: str, user_id: str) -> Upload:
        """Fetch a library item scoped to its owner, or raise the uniform error."""
        upload = self.upload_repo.get_by_id(item_id, user_id)
        if not upload:
            raise ValueError("Library item not found")
        return upload

    def _validate_tag_ids(self, tag_ids: List[str], user_id: str) -> None:
        """Every tag must be an UPLOAD tag owned by this user.

        Without this a caller could attach a tag belonging to someone else -
        or a global MODEL tag - and have it come back in their library payload.
        """
        for tag_id in tag_ids:
            tag = self.tag_repo.get_tag_by_id(tag_id)
            if not tag or tag.type != TagType.UPLOAD.value or tag.user_id != user_id:
                raise ValueError(f"Invalid tag ID: {tag_id}")

    def _to_item(self, upload: Upload, tags: List[Any]) -> LibraryItem:
        """Serialize an upload row plus its tags into the API shape."""
        return LibraryItem(
            id=upload.id,
            filename=upload.filename,
            original_filename=upload.original_filename,
            media_type=upload.media_type,
            mime_type=upload.mime_type,
            url=f"/api/media/uploads/{upload.filename}",
            thumbnail_small=f"/api/media/uploads/{upload.filename}?size=small" if upload.thumbnail_small else None,
            thumbnail_medium=f"/api/media/uploads/{upload.filename}?size=medium" if upload.thumbnail_medium else None,
            thumbnail_large=f"/api/media/uploads/{upload.filename}?size=large" if upload.thumbnail_large else None,
            width=upload.width,
            height=upload.height,
            duration_seconds=upload.duration_seconds,
            fps=upload.fps,
            size=upload.file_size,
            created_at=upload.created_at.isoformat() if upload.created_at else None,
            tags=[tag.model_dump() for tag in tags],
        )
