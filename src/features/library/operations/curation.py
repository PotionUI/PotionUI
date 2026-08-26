"""Library curation operations (tag assignment)."""
from typing import Any, Dict, List

from src.features.library.collaborators import LibraryCollaborators
from src.features.library.operations.guards import get_owned_or_raise, validate_tag_ids


def set_tags(
    collaborators: LibraryCollaborators, item_id: str, tag_ids: List[str], user_id: str
) -> List[Dict[str, Any]]:
    """Replace a library item's tags.

    Raises:
        ValueError: If the item is not owned by this user, or a tag is not an
            UPLOAD tag they own.
    """
    get_owned_or_raise(collaborators, item_id, user_id)
    tag_ids = [t for t in (tag_ids or []) if t]
    validate_tag_ids(collaborators, tag_ids, user_id)

    collaborators.tag_repository.set_upload_tags(item_id, tag_ids)
    return [tag.model_dump() for tag in collaborators.tag_repository.get_upload_tags(item_id)]
