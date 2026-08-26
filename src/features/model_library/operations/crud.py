"""
Create, rename and delete a model collection.

Module-level functions, `ModelCollectionRepository` as the leading arg - no
class holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"invalid" (the controller converts that to an HTTP response).
"""
import logging
from typing import Optional

from src.features.model_library.operations.reads import get_collection
from src.features.model_library.records.model_collection import ModelCollection
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository

logger = logging.getLogger(__name__)


def create_collection(
    model_collection_repository: ModelCollectionRepository, name: str, user_id: str, parent_id: Optional[str] = None
) -> ModelCollection:
    """
    Create a new model collection, optionally nested under a parent folder.

    Raises:
        ValueError: If the name is empty, or the parent is not found/owned.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name is required")

    if parent_id:
        # Ownership/existence check (raises if not found/owned)
        get_collection(model_collection_repository, parent_id, user_id)

    collection = model_collection_repository.create(name, user_id, parent_id)
    logger.info(f"Model collection created: {collection.name} (id: {collection.id})")
    return collection


def rename_collection(model_collection_repository: ModelCollectionRepository, collection_id: str, name: str, user_id: str) -> ModelCollection:
    """
    Rename a model collection owned by the user.

    Raises:
        ValueError: If the name is empty, or the collection is not found/owned.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Collection name is required")

    success = model_collection_repository.rename(collection_id, name, user_id)
    if not success:
        raise ValueError("Collection not found or access denied")

    return get_collection(model_collection_repository, collection_id, user_id)


def delete_collection(model_collection_repository: ModelCollectionRepository, collection_id: str, user_id: str) -> bool:
    """
    Delete a model collection owned by the user (cascade removes memberships).

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    success = model_collection_repository.delete(collection_id, user_id)
    if not success:
        raise ValueError("Collection not found or access denied")

    logger.info(f"Model collection deleted (id: {collection_id})")
    return True
