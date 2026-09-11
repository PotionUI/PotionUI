"""Deleting library items by criteria - tags (ALL of them), age, a created
date range, and media type. Always scoped to one owner: the library has no
cross-user delete story (unlike generation history's admin housekeeping,
`src.features.generation.criteria_delete`).

Deletion runs each candidate through the same per-item delete
(`src.features.library.operations.mutations.delete_item`) a single delete
uses, so a file leaves disk exactly the same way.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.features.library.collaborators import LibraryCollaborators
from src.features.library.operations.guards import validate_tag_ids
from src.features.library.operations.mutations import delete_item
from src.features.library.operations.reads import LIBRARY_MEDIA_TYPES


@dataclass(frozen=True)
class LibraryDeleteCriteria:
    user_id: str
    tag_ids: Optional[List[str]] = None
    older_than_days: Optional[int] = None
    created_from: Optional[str] = None
    created_to: Optional[str] = None
    media_type: Optional[str] = None

    def is_empty(self) -> bool:
        """No criterion narrows the candidate set - deleting under this
        would sweep the user's entire library."""
        return (
            not self.tag_ids
            and self.older_than_days is None
            and self.created_from is None
            and self.created_to is None
            and not self.media_type
        )


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        pass
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        raise ValueError(f"Invalid date format: {value}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")


def _validate_date_range(created_from: Optional[str], created_to: Optional[str]) -> None:
    parsed_from = _parse_date(created_from) if created_from else None
    parsed_to = _parse_date(created_to) if created_to else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise ValueError("created_from date must be before created_to date")


def owner_criteria(collaborators: LibraryCollaborators, request, user_id: str) -> LibraryDeleteCriteria:
    """Build a `LibraryDeleteCriteria` scoped to `user_id`, validating tag
    ownership, media type and date filters the same way the generation
    history criteria-delete flow validates its own request."""
    if request.tag_ids:
        validate_tag_ids(collaborators, request.tag_ids, user_id)
    if request.media_type and request.media_type not in LIBRARY_MEDIA_TYPES:
        raise ValueError(f"Invalid media type: {request.media_type}")
    if request.older_than_days is not None and request.older_than_days < 0:
        raise ValueError("older_than_days must not be negative")
    _validate_date_range(request.created_from, request.created_to)

    return LibraryDeleteCriteria(
        user_id=user_id,
        tag_ids=request.tag_ids or None,
        older_than_days=request.older_than_days,
        created_from=request.created_from,
        created_to=request.created_to,
        media_type=request.media_type,
    )


def _find(collaborators: LibraryCollaborators, criteria: LibraryDeleteCriteria) -> List[str]:
    return collaborators.repository.find_ids_by_criteria(
        user_id=criteria.user_id,
        tag_ids=criteria.tag_ids,
        older_than_days=criteria.older_than_days,
        created_from=criteria.created_from,
        created_to=criteria.created_to,
        media_type=criteria.media_type,
    )


def preview_items(collaborators: LibraryCollaborators, criteria: LibraryDeleteCriteria) -> int:
    """How many library items `criteria` matches right now."""
    return len(_find(collaborators, criteria))


def delete_items(collaborators: LibraryCollaborators, criteria: LibraryDeleteCriteria) -> Dict[str, Any]:
    """Delete every library item matching `criteria`, one at a time through
    the same path a single delete uses. A candidate that vanishes between the
    find and the delete (raced by another request) is simply not counted."""
    deleted_count = 0
    for item_id in _find(collaborators, criteria):
        try:
            delete_item(collaborators, item_id, criteria.user_id)
            deleted_count += 1
        except ValueError:
            continue

    return {"deleted_count": deleted_count, "files_deleted": deleted_count}
