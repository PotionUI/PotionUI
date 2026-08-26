"""
Reparent one or several collections in the folder tree.

Module-level functions, `CollectionRepository` as the leading arg - no class
holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"cycle" (the controller converts that to an HTTP response).
"""
from typing import List, Optional

from src.features.collections.operations.reads import get_collection
from src.features.collections.records import Collection
from src.features.collections.repository import CollectionRepository


def move_collection(
    collection_repository: CollectionRepository, collection_id: str, new_parent_id: Optional[str], user_id: str, scope: str
) -> Collection:
    """
    Reparent a collection under a new parent folder (or to the root), within scope.

    Raises:
        ValueError: If the collection/parent is not found/owned/same-scope, or the
            move would create a cycle.
    """
    # Ownership + scope check (raises if not found/owned)
    get_collection(collection_repository, collection_id, user_id, scope)
    if new_parent_id:
        # Cross-scope target rejected here: a parent in the other scope
        # simply isn't found under this scope's lookup.
        get_collection(collection_repository, new_parent_id, user_id, scope)

    success = collection_repository.move(collection_id, new_parent_id, user_id, scope)
    if not success:
        raise ValueError("Collection not found or access denied")

    return get_collection(collection_repository, collection_id, user_id, scope)


def bulk_move_collections(
    collection_repository: CollectionRepository, collection_ids: List[str], new_parent_id: Optional[str], user_id: str, scope: str
) -> dict:
    """
    Reparent several collections under one new parent (or to the root) in a
    single call, all within the same scope. Unlike `move_collection`, a bad
    id doesn't fail the whole batch - each id is validated and applied
    independently, and the result reports which ones landed.

    The target is resolved once up front (a missing/foreign/cross-scope
    parent fails every id the same way). Each id is then moved through
    `CollectionRepository.move`, which rejects - and never applies - a
    cycle: the target being the id itself or one of its own descendants.
    A cycle for one id never blocks an unrelated id in the same batch;
    moving a folder and its own descendant together is a client-side
    concern (the descendant is redundant once its ancestor moves), not
    something this function needs to reason about.
    """
    if not collection_ids:
        return {"moved": 0, "failed": 0, "errors": []}

    target_error: Optional[str] = None
    if new_parent_id is not None:
        try:
            get_collection(collection_repository, new_parent_id, user_id, scope)
        except ValueError as e:
            target_error = str(e)

    moved = 0
    errors = []
    for collection_id in collection_ids:
        try:
            if target_error is not None:
                raise ValueError(target_error)
            success = collection_repository.move(collection_id, new_parent_id, user_id, scope)
            if not success:
                raise ValueError("Collection not found or access denied")
            moved += 1
        except ValueError as e:
            errors.append({"id": collection_id, "reason": str(e)})

    return {"moved": moved, "failed": len(errors), "errors": errors}
