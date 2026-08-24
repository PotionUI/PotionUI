"""
Collections domain manager.

Handles all business logic for collections. Framework-agnostic - uses ValueError
for errors (controller converts to HTTP responses). Collections are always
user-scoped: every operation verifies ownership. They are also scope-scoped
('history' | 'library', migration 137) - every operation takes the caller's
scope and passes it straight through to the repository, so a History caller
can never read, mutate, or reparent a Library collection and vice versa. There
is no default: a caller that forgets to pass scope fails loudly rather than
silently touching the wrong tree.
"""
import logging
from typing import List, Optional


from src.features.collections.records import Collection
from src.features.collections.repository import CollectionRepository

logger = logging.getLogger(__name__)


class CollectionManager:
    """
    Coordinates collection operations.

    Handles CRUD for collections plus membership management. All collections are
    user-owned; the manager enforces ownership before mutating.
    """

    def __init__(self, collection_repository: CollectionRepository):
        self.repository = collection_repository

    # ========== Read Operations ==========

    def list_collections(self, user_id: str, scope: str) -> List[Collection]:
        """Get all collections owned by the user within scope, each with a generation count."""
        return self.repository.list(user_id, scope)

    def get_collection(self, collection_id: str, user_id: str, scope: str) -> Collection:
        """
        Get a specific collection by ID (scoped to owner and to scope).

        Raises:
            ValueError: If collection not found, access denied, or in a different scope.
        """
        collection = self.repository.get_by_id(collection_id, scope, user_id)
        if not collection:
            raise ValueError("Collection not found or access denied")
        return collection

    # ========== Create Operations ==========

    def create_collection(
        self, name: str, user_id: str, scope: str, parent_id: Optional[str] = None
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
            self.get_collection(parent_id, user_id, scope)

        collection = self.repository.create(name, user_id, scope, parent_id)
        logger.info(f"Collection created: {collection.name} (id: {collection.id}, scope: {scope})")
        return collection

    def move_collection(
        self, collection_id: str, new_parent_id: Optional[str], user_id: str, scope: str
    ) -> Collection:
        """
        Reparent a collection under a new parent folder (or to the root), within scope.

        Raises:
            ValueError: If the collection/parent is not found/owned/same-scope, or the
                move would create a cycle.
        """
        # Ownership + scope check (raises if not found/owned)
        self.get_collection(collection_id, user_id, scope)
        if new_parent_id:
            # Cross-scope target rejected here: a parent in the other scope
            # simply isn't found under this scope's lookup.
            self.get_collection(new_parent_id, user_id, scope)

        success = self.repository.move(collection_id, new_parent_id, user_id, scope)
        if not success:
            raise ValueError("Collection not found or access denied")

        return self.get_collection(collection_id, user_id, scope)

    def bulk_move_collections(
        self, collection_ids: List[str], new_parent_id: Optional[str], user_id: str, scope: str
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
        something this method needs to reason about.
        """
        if not collection_ids:
            return {"moved": 0, "failed": 0, "errors": []}

        target_error: Optional[str] = None
        if new_parent_id is not None:
            try:
                self.get_collection(new_parent_id, user_id, scope)
            except ValueError as e:
                target_error = str(e)

        moved = 0
        errors = []
        for collection_id in collection_ids:
            try:
                if target_error is not None:
                    raise ValueError(target_error)
                success = self.repository.move(collection_id, new_parent_id, user_id, scope)
                if not success:
                    raise ValueError("Collection not found or access denied")
                moved += 1
            except ValueError as e:
                errors.append({"id": collection_id, "reason": str(e)})

        return {"moved": moved, "failed": len(errors), "errors": errors}

    # ========== Update Operations ==========

    def rename_collection(self, collection_id: str, name: str, user_id: str, scope: str) -> Collection:
        """
        Rename a collection owned by the user, within scope.

        Raises:
            ValueError: If the name is empty, or the collection is not found/owned/same-scope.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("Collection name is required")

        success = self.repository.rename(collection_id, name, user_id, scope)
        if not success:
            raise ValueError("Collection not found or access denied")

        return self.get_collection(collection_id, user_id, scope)

    # ========== Delete Operations ==========

    def delete_collection(self, collection_id: str, user_id: str, scope: str) -> bool:
        """
        Delete a collection owned by the user, within scope (cascade removes memberships).

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        success = self.repository.delete(collection_id, user_id, scope)
        if not success:
            raise ValueError("Collection not found or access denied")

        logger.info(f"Collection deleted (id: {collection_id}, scope: {scope})")
        return True

    # ========== Membership Operations ==========

    def add_members(self, collection_id: str, generation_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add generations to a collection owned by the user, within scope.

        Duplicate memberships are ignored. Returns the number newly added.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.add_members(collection_id, generation_ids, user_id, scope)

    def remove_members(self, collection_id: str, generation_ids: List[str], user_id: str, scope: str) -> int:
        """
        Remove generations from a collection owned by the user, within scope.

        Returns the number of memberships removed.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.remove_members(collection_id, generation_ids)

    def add_upload_members(self, collection_id: str, upload_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add library uploads to a collection owned by the user, within scope.

        Uploads the user does not own are skipped, not reported - see
        `CollectionRepository.add_upload_members`. Returns the number newly added.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.add_upload_members(collection_id, upload_ids, user_id, scope)

    def remove_upload_members(self, collection_id: str, upload_ids: List[str], user_id: str, scope: str) -> int:
        """
        Remove library uploads from a collection owned by the user, within scope.

        Returns the number of memberships removed.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.remove_upload_members(collection_id, upload_ids)

    def add_prompt_members(self, collection_id: str, prompt_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add saved prompts to a collection owned by the user, within scope.

        Prompts the user does not own are skipped, not reported - see
        `CollectionRepository.add_prompt_members`. Returns the number newly added.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.add_prompt_members(collection_id, prompt_ids, user_id, scope)

    def remove_prompt_members(self, collection_id: str, prompt_ids: List[str], user_id: str, scope: str) -> int:
        """
        Remove saved prompts from a collection owned by the user, within scope.

        Returns the number of memberships removed.

        Raises:
            ValueError: If the collection is not found or access denied.
        """
        # Ownership + scope check (also raises if not found)
        self.get_collection(collection_id, user_id, scope)
        return self.repository.remove_prompt_members(collection_id, prompt_ids)
