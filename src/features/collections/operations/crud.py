"""
Create, rename and delete a collection.

Module-level functions, `CollectionRepository` as the leading arg - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"invalid" (the controller converts that to an HTTP response).
"""
import logging
from typing import Optional

from src.features.collections.operations.reads import get_collection
from src.features.collections.records import Collection
from src.features.collections.repository import CollectionRepository

logger = logging.getLogger(__name__)


def create_collection(
    collection_repository: CollectionRepository, name: str, user_id: str, scope: str, parent_id: Optional[str] = None
) -> Collection:
    """
    Create a new collection, optionally nested under a parent folder in the same scope.

    Raises:
        ValueError: If the name is empty, or the parent is not found/owned/same-scope.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name is required")

    if parent_id:
        # Ownership/scope check (raises if not found/owned/cross-scope)
        get_collection(collection_repository, parent_id, user_id, scope)

    collection = collection_repository.create(name, user_id, scope, parent_id)
    logger.info(f"Collection created: {collection.name} (id: {collection.id}, scope: {scope})")
    return collection


def rename_collection(collection_repository: CollectionRepository, collection_id: str, name: str, user_id: str, scope: str) -> Collection:
    """
    Rename a collection owned by the user, within scope.

    Raises:
        ValueError: If the name is empty, or the collection is not found/owned/same-scope.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name is required")

    success = collection_repository.rename(collection_id, name, user_id, scope)
    if not success:
        raise ValueError("Collection not found or access denied")

    return get_collection(collection_repository, collection_id, user_id, scope)


def delete_collection(collection_repository: CollectionRepository, collection_id: str, user_id: str, scope: str) -> bool:
    """
    Delete a collection owned by the user, within scope (cascade removes memberships).

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    success = collection_repository.delete(collection_id, user_id, scope)
    if not success:
        raise ValueError("Collection not found or access denied")

    logger.info(f"Collection deleted (id: {collection_id}, scope: {scope})")
    return True
