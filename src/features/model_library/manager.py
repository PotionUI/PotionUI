"""
Model library domain manager.

Handles all business logic for the user-level model library: model collections
(named, user-owned virtual groupings of models) plus per-user favorite/custom-name
overlays on models. Framework-agnostic - uses ValueError for errors (controller
converts to HTTP responses). Mirrors CollectionManager for the collection half.
"""
import logging
from typing import List, Optional


from src.features.model_library.records.model_collection import ModelCollection
from src.features.model_library.records.user_model_meta import UserModelMeta
from src.features.model_library.repository.model_collection_repository import ModelCollectionRepository
from src.features.model_library.repository.user_model_meta_repository import UserModelMetaRepository

logger = logging.getLogger(__name__)


class ModelLibraryManager:
    """
    Coordinates model library operations.

    Handles CRUD for model collections plus membership management, and
    per-user favorite/custom-name overlays on models. All collections and
    overlays are user-owned/user-scoped; the manager enforces ownership before
    mutating.
    """

    def __init__(
        self,
        model_collection_repository: ModelCollectionRepository,
        user_model_meta_repository: UserModelMetaRepository
    ):
        self.collection_repository = model_collection_repository
        self.meta_repository = user_model_meta_repository

    # ========== Collection Read Operations ==========

    def list_collections(self, user_id: str) -> List[ModelCollection]:
        """Get all model collections owned by the user, each with a model count."""
        return self.collection_repository.list(user_id)

    def get_collection(self, collection_id: str, user_id: str) -> ModelCollection:
        """
        Get a specific model collection by ID (scoped to owner).

        Raises:
            ValueError: If collection not found or access denied.
        """
        collection = self.collection_repository.get_by_id(collection_id, user_id)
        if not collection:
            raise ValueError("Collection not found or access denied")
        return collection

    # ========== Collection Create Operations ==========

    def create_collection(self, name: str, user_id: str, parent_id: Optional[str] = None) -> ModelCollection:
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
            self.get_collection(parent_id, user_id)

        collection = self.collection_repository.create(name, user_id, parent_id)
        logger.info(f"Model collection created: {collection.name} (id: {collection.id})")
        return collection

    def move_collection(self, collection_id: str, new_parent_id: Optional[str], user_id: str) -> ModelCollection:
        """
        Reparent a model collection under a new parent folder (or to the root).

        Raises:
            ValueError: If the collection/parent is not found/owned, or the move
                would create a cycle.
        """
        # Ownership check (raises if not found/owned)
        self.get_collection(collection_id, user_id)
        if new_parent_id:
            self.get_collection(new_parent_id, user_id)

        success = self.collection_repository.move(collection_id, new_parent_id, user_id)
        if not success:
            raise ValueError("Collection not found or access denied")

        return self.get_collection(collection_id, user_id)

    def bulk_move_collections(
        self, collection_ids: List[str], new_parent_id: Optional[str], user_id: str
    ) -> dict:
        """
        Reparent several model collections under one new parent (or to the
        root) in a single call. Unlike `move_collection`, a bad id doesn't
        fail the whole batch - each id is validated and applied
        independently, and the result reports which ones landed.

        The target is resolved once up front (a missing/foreign parent fails
        every id the same way). Each id is then moved through
        `ModelCollectionRepository.move`, which rejects - and never applies -
        a cycle: the target being the id itself or one of its own
        descendants. A cycle for one id never blocks an unrelated id in the
        same batch; moving a folder and its own descendant together is a
        client-side concern (the descendant is redundant once its ancestor
        moves), not something this method needs to reason about.
        """
        if not collection_ids:
            return {"moved": 0, "failed": 0, "errors": []}

        target_error: Optional[str] = None
        if new_parent_id is not None:
            try:
                self.get_collection(new_parent_id, user_id)
            except ValueError as e:
                target_error = str(e)

        moved = 0
        errors = []
        for collection_id in collection_ids:
            try:
                if target_error is not None:
                    raise ValueError(target_error)
                success = self.collection_repository.move(collection_id, new_parent_id, user_id)
                if not success:
                    raise ValueError("Collection not found or access denied")
                moved += 1
            except ValueError as e:
                errors.append({"id": collection_id, "reason": str(e)})

        return {"moved": moved, "failed": len(errors), "errors": errors}

    # ========== Collection Update Operations ==========

    def rename_collection(self, collection_id: str, name: str, user_id: str) -> ModelCollection:
        """
        Rename a model collection owned by the user.

        Raises:
            ValueError: If the name is empty, or the collection is not found/owned.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Collection name is required")

        success = self.collection_repository.rename(collection_id, name, user_id)
        if not success:
            raise ValueError("Collection not found or access denied")

        return self.get_collection(collection_id, user_id)

    # ========== Collection Delete Operations ==========

    def delete_collection(self, collection_id: str, user_id: str) -> bool:
        """
        Delete a model collection owned by the user (cascade removes memberships).

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        success = self.collection_repository.delete(collection_id, user_id)
        if not success:
            raise ValueError("Collection not found or access denied")

        logger.info(f"Model collection deleted (id: {collection_id})")
        return True

    # ========== Collection Membership Operations ==========

    def add_members(self, collection_id: str, model_ids: List[str], user_id: str) -> int:
        """
        Add models to a collection owned by the user.

        Duplicate memberships are ignored. Returns the number newly added.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership check (also raises if not found)
        self.get_collection(collection_id, user_id)
        return self.collection_repository.add_members(collection_id, model_ids, user_id)

    def remove_members(self, collection_id: str, model_ids: List[str], user_id: str) -> int:
        """
        Remove models from a collection owned by the user.

        Returns the number of memberships removed.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership check (also raises if not found)
        self.get_collection(collection_id, user_id)
        return self.collection_repository.remove_members(collection_id, model_ids)

    # ========== Model Overlay Operations ==========

    def set_favorite(self, user_id: str, model_id: str, is_favorite: bool) -> UserModelMeta:
        """Set (or clear) the favorite flag for a model, scoped to the user."""
        return self.meta_repository.set_favorite(user_id, model_id, is_favorite)

    def set_custom_name(self, user_id: str, model_id: str, name: Optional[str]) -> UserModelMeta:
        """Set (or clear, when name is None) a per-user custom display name for a model."""
        if name is not None:
            name = name.strip() or None
        return self.meta_repository.set_custom_name(user_id, model_id, name)
