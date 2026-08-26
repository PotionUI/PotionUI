"""Per-user inspiration collection operations."""
from typing import Optional

from src.features.inspirations.collaborators import InspirationCollaborators
from src.features.inspirations.records import InspirationCollection


def create_collection(
    collaborators: InspirationCollaborators, user_id: str, name: str, parent_id: Optional[str] = None
) -> InspirationCollection:
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name is required")
    if parent_id and not collaborators.repository.get_collection(parent_id, user_id):
        raise ValueError("Parent collection not found or access denied")
    return collaborators.repository.create_collection(user_id, name, parent_id)


def update_collection(
    collaborators: InspirationCollaborators,
    collection_id: str,
    user_id: str,
    name: Optional[str] = None,
    parent_id: Optional[str] = None,
    parent_id_set: bool = False,
) -> InspirationCollection:
    """Rename and/or reparent a collection owned by the user.

    `parent_id_set` distinguishes "the request omitted parent_id" (leave it
    alone) from "the request set it to null" (move to root) - both look like
    `parent_id=None` otherwise.

    Raises:
        ValueError: If the collection/new parent is not found/owned, or the
            move would create a cycle.
    """
    if not collaborators.repository.get_collection(collection_id, user_id):
        raise ValueError("Collection not found or access denied")

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Collection name is required")
        collaborators.repository.rename_collection(collection_id, user_id, name)

    if parent_id_set:
        if parent_id and not collaborators.repository.get_collection(parent_id, user_id):
            raise ValueError("Parent collection not found or access denied")
        if collaborators.repository.creates_cycle(collection_id, parent_id):
            raise ValueError("Cannot move a collection into itself or one of its subfolders")
        collaborators.repository.move_collection(collection_id, user_id, parent_id)

    updated = collaborators.repository.get_collection(collection_id, user_id)
    if updated is None:
        raise ValueError("Collection not found or access denied")
    return updated


def delete_collection(collaborators: InspirationCollaborators, collection_id: str, user_id: str) -> None:
    if not collaborators.repository.delete_collection(collection_id, user_id):
        raise ValueError("Collection not found or access denied")


def add_item(collaborators: InspirationCollaborators, collection_id: str, user_id: str, inspiration_id: str) -> None:
    if not collaborators.repository.get_collection(collection_id, user_id):
        raise ValueError("Collection not found or access denied")
    if not collaborators.repository.get_by_id(inspiration_id):
        raise ValueError("Inspiration not found")
    collaborators.repository.add_item(collection_id, inspiration_id)


def remove_item(collaborators: InspirationCollaborators, collection_id: str, user_id: str, inspiration_id: str) -> None:
    if not collaborators.repository.get_collection(collection_id, user_id):
        raise ValueError("Collection not found or access denied")
    collaborators.repository.remove_item(collection_id, inspiration_id)
