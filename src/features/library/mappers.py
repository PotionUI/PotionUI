"""Mapping between library storage rows and the API shape.

Kept apart from `operations/` (which owns behavior) and `repository.py`
(which owns queries) - this module only ever turns a row plus its tags into a
`LibraryItem`, or a filename into its storage key.
"""
from typing import Any, List

from src.features.library.dto import LibraryItem
from src.features.media.records import Upload
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key


def upload_key(filename: str) -> str:
    """The storage key for an item in the uploads namespace.

    Same containment guarantee `FilePathResolver.resolve_upload_file` gave a
    filesystem path - `validate_key` raises on traversal/absolute input,
    translated to the `ValueError` callers already expect.
    """
    try:
        return uploads_key(filename)
    except StorageKeyError:
        raise ValueError("Access denied - path traversal detected")


def upload_to_item(upload: Upload, tags: List[Any]) -> LibraryItem:
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
