"""
Add/remove models to/from a model collection.

Module-level functions, `ModelCollectionRepository` as the leading arg - no
class holds them together. Framework-agnostic - uses ``ValueError`` for
"not found" (the controller converts that to an HTTP response).
"""
from typing import List

from src.features.model_library.operations.reads import get_collection
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository


def add_members(model_collection_repository: ModelCollectionRepository, collection_id: str, model_ids: List[str], user_id: str) -> int:
    """
    Add models to a collection owned by the user.

    Duplicate memberships are ignored. Returns the number newly added.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership check (also raises if not found)
    get_collection(model_collection_repository, collection_id, user_id)
    return model_collection_repository.add_members(collection_id, model_ids, user_id)


def remove_members(model_collection_repository: ModelCollectionRepository, collection_id: str, model_ids: List[str], user_id: str) -> int:
    """
    Remove models from a collection owned by the user.

    Returns the number of memberships removed.

    Raises:
        ValueError: If the collection is not found or access denied.
    """
    # Ownership check (also raises if not found)
    get_collection(model_collection_repository, collection_id, user_id)
    return model_collection_repository.remove_members(collection_id, model_ids)
