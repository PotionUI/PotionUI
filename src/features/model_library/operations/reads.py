"""
Resolve a single model collection by id, enforcing ownership.

Not a route (there's no `GET /{collection_id}`) - this is the shared
"resolve or raise" building block every mutation in this package needs.
"""
from src.features.model_library.records.model_collection import ModelCollection
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository


def get_collection(model_collection_repository: ModelCollectionRepository, collection_id: str, user_id: str) -> ModelCollection:
    """
    Get a specific model collection by ID (scoped to owner).

    Raises:
        ValueError: If collection not found or access denied.
    """
    collection = model_collection_repository.get_by_id(collection_id, user_id)
    if not collection:
        raise ValueError("Collection not found or access denied")
    return collection
