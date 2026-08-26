"""Shared preconditions library operations open with: an item must be owned
by the requesting user, and any tag attached to it must be an UPLOAD tag that
user owns. Module-level functions, no class holds them - every operation in
this package calls these two directly rather than through a shared base.
"""
from typing import List

from src.features.library.collaborators import LibraryCollaborators
from src.features.media.records import Upload
from src.features.tags.dto import TagType


def get_owned_or_raise(collaborators: LibraryCollaborators, item_id: str, user_id: str) -> Upload:
    """Fetch a library item scoped to its owner, or raise the uniform error."""
    upload = collaborators.upload_repository.get_by_id(item_id, user_id)
    if not upload:
        raise ValueError("Library item not found")
    return upload


def validate_tag_ids(collaborators: LibraryCollaborators, tag_ids: List[str], user_id: str) -> None:
    """Every tag must be an UPLOAD tag owned by this user.

    Without this a caller could attach a tag belonging to someone else - or a
    global MODEL tag - and have it come back in their library payload.
    """
    for tag_id in tag_ids:
        tag = collaborators.tag_repository.get_tag_by_id(tag_id)
        if not tag or tag.type != TagType.UPLOAD.value or tag.user_id != user_id:
            raise ValueError(f"Invalid tag ID: {tag_id}")
