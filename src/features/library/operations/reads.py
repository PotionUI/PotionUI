"""Library read operations.

Not bare repository delegation - each of these validates the caller's
filters (media type, tag ownership), clamps paging, or resolves an ownership
guard, then maps the result onto the API shape. `LibraryController`
(`routes.py`) calls these directly rather than reaching into the repository
itself, the way it would for a genuinely bare read.
"""
from typing import Any, Dict, List, Optional

from src.features.library.collaborators import LibraryCollaborators
from src.features.library.dto import LibraryFacets, LibraryItem, LibraryListResult
from src.features.library.mappers import upload_to_item
from src.features.library.operations.guards import get_owned_or_raise, validate_tag_ids

MAX_LIBRARY_LIST_LIMIT = 200

# The media kinds a library resource can be. Mirrors the media-loader upload
# gate (`MediaTypeResolver.is_valid_media_type`) - a mesh or anything else a
# pipe can emit has no place in a field a user picks an image or clip from.
LIBRARY_MEDIA_TYPES = ("image", "video", "audio")


def list_items(
    collaborators: LibraryCollaborators,
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
        validate_tag_ids(collaborators, tag_ids, user_id)

    limit = max(1, min(limit, MAX_LIBRARY_LIST_LIMIT))
    offset = max(0, offset)

    filters = dict(
        media_type=media_type,
        tag_ids=tag_ids or None,
        collection_id=collection_id,
        search=(search or "").strip() or None,
    )

    uploads = collaborators.repository.list_items(user_id, limit=limit, offset=offset, **filters)
    total = collaborators.repository.count_items(user_id, **filters)
    tags_by_id = collaborators.tag_repository.get_upload_tags_bulk([u.id for u in uploads])

    return LibraryListResult(
        items=[upload_to_item(u, tags_by_id.get(u.id, [])) for u in uploads],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_facets(collaborators: LibraryCollaborators, user_id: str) -> LibraryFacets:
    """Per-media-type counts for the user's whole library."""
    return LibraryFacets(media_types=collaborators.repository.media_type_counts(user_id))


def get_item(collaborators: LibraryCollaborators, item_id: str, user_id: str) -> LibraryItem:
    """One library item owned by the user.

    Raises:
        ValueError: If no such item is owned by this user.
    """
    upload = get_owned_or_raise(collaborators, item_id, user_id)
    return upload_to_item(upload, collaborators.tag_repository.get_upload_tags(upload.id))


def get_tags(collaborators: LibraryCollaborators, item_id: str, user_id: str) -> List[Dict[str, Any]]:
    """Tags on one library item owned by the user."""
    get_owned_or_raise(collaborators, item_id, user_id)
    return [tag.model_dump() for tag in collaborators.tag_repository.get_upload_tags(item_id)]
