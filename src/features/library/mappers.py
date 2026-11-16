"""Mapping between library storage rows and the API shape.

Kept apart from `operations/` (which owns behavior) and `repository.py`
(which owns queries) - this module only ever turns a row plus its tags into a
`LibraryItem`, or a filename into its storage key.
"""
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from src.features.library.dto import LibraryItem, MadeInEditor
from src.features.media.records import Upload
from src.platform.database.rows import dt_iso
from src.platform.filesystem.storage_driver import StorageKeyError, uploads_key

if TYPE_CHECKING:
    from src.features.edits.repository import EditRepository


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


def _parse_edit_link(upload: Upload) -> Optional[Dict[str, Any]]:
    link = (upload.provenance or {}).get("edit")
    if not isinstance(link, dict):
        return None
    edit_id = link.get("id")
    if not isinstance(edit_id, str) or not edit_id:
        return None
    return link


def made_in_editor_for(
    edit_repository: Optional["EditRepository"], user_id: str, uploads: Sequence[Upload]
) -> Dict[str, MadeInEditor]:
    links = {u.id: link for u in uploads if (link := _parse_edit_link(u)) is not None}
    if not links:
        return {}
    alive = (
        edit_repository.existing_ids(user_id, [link["id"] for link in links.values()])
        if edit_repository is not None
        else set()
    )
    result: Dict[str, MadeInEditor] = {}
    for upload_id, link in links.items():
        tool = link.get("tool")
        revision = link.get("revision")
        result[upload_id] = MadeInEditor(
            edit_id=link["id"],
            tool=tool if isinstance(tool, str) else None,
            revision=revision if isinstance(revision, int) and not isinstance(revision, bool) else None,
            available=link["id"] in alive,
            continuable=link.get("continuable") is True,
        )
    return result


def upload_to_item(
    upload: Upload, tags: List[Any], made_in_editor: Optional[MadeInEditor] = None
) -> LibraryItem:
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
        created_at=dt_iso(upload.created_at),
        tags=[tag.model_dump() for tag in tags],
        is_favorite=upload.is_favorite,
        has_alpha=upload.has_alpha,
        has_audio=upload.has_audio,
        source_generation_id=upload.source_generation_id,
        provenance=upload.provenance,
        made_in_editor=made_in_editor,
    )
