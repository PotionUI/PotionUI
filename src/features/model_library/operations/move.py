"""
Reparent one or several model collections in the folder tree.

Module-level functions, `ModelCollectionRepository` as the leading arg - no
class holds them together. Framework-agnostic - uses ``ValueError`` for
"not found"/"cycle" (the controller converts that to an HTTP response).
"""
from typing import List, Optional

from src.features.model_library.operations.reads import get_collection
from src.features.model_library.records.model_collection import ModelCollection
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository


def move_collection(
    model_collection_repository: ModelCollectionRepository, collection_id: str, new_parent_id: Optional[str], user_id: str
) -> ModelCollection:
    """
    Reparent a model collection under a new parent folder (or to the root).

    Raises:
        ValueError: If the collection/parent is not found/owned, or the move
            would create a cycle.
    """
    # Ownership check (raises if not found/owned)
    get_collection(model_collection_repository, collection_id, user_id)
    if new_parent_id:
        get_collection(model_collection_repository, new_parent_id, user_id)

    success = model_collection_repository.move(collection_id, new_parent_id, user_id)
    if not success:
        raise ValueError("Collection not found or access denied")

    return get_collection(model_collection_repository, collection_id, user_id)


def bulk_move_collections(
    model_collection_repository: ModelCollectionRepository, collection_ids: List[str], new_parent_id: Optional[str], user_id: str
) -> dict:
    """
    Reparent several model collections under one new parent (or to the root)
    in a single call. Unlike `move_collection`, a bad id doesn't fail the
    whole batch - each id is validated and applied independently, and the
    result reports which ones landed.

    The target is resolved once up front (a missing/foreign parent fails
    every id the same way). Each id is then moved through
    `ModelCollectionRepository.move`, which rejects - and never applies - a
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
            get_collection(model_collection_repository, new_parent_id, user_id)
        except ValueError as e:
            target_error = str(e)

    moved = 0
    errors = []
    for collection_id in collection_ids:
        try:
            if target_error is not None:
                raise ValueError(target_error)
            success = model_collection_repository.move(collection_id, new_parent_id, user_id)
            if not success:
                raise ValueError("Collection not found or access denied")
            moved += 1
        except ValueError as e:
            errors.append({"id": collection_id, "reason": str(e)})

    return {"moved": moved, "failed": len(errors), "errors": errors}
