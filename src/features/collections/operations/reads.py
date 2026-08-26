"""
Resolve a single collection by id, enforcing ownership and scope.

Not a route (there's no `GET /{collection_id}`) - this is the shared
"resolve or raise" building block every mutation in this package needs, and
that outside callers (the `manage_collections` chat/MCP tool) also reach for
directly before previewing a change.
"""
from src.features.collections.records import Collection
from src.features.collections.repository import CollectionRepository


def get_collection(collection_repository: CollectionRepository, collection_id: str, user_id: str, scope: str) -> Collection:
    """
    Get a specific collection by ID (scoped to owner and to scope).

    Raises:
        ValueError: If collection not found, access denied, or in a different scope.
    """
    collection = collection_repository.get_by_id(collection_id, scope, user_id)
    if not collection:
        raise ValueError("Collection not found or access denied")
    return collection
