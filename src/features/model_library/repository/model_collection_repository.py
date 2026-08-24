"""
Model Collection Repository

Handles database operations for model collections (named, user-owned virtual
groupings of models) and the model_collection_members junction table. Mirrors
CollectionRepository (generation collections) with `model_collections` /
`model_collection_members` / `model_id` in place of `collections` /
`collection_generations` / `generation_id`.
"""
from typing import List, Optional
from datetime import datetime
from src.platform.database import db
from src.features.model_library.records.model_collection import ModelCollection
from src.platform.util.ids import generate_ulid
import logging

logger = logging.getLogger(__name__)


class ModelCollectionRepository:
    """Repository for managing model collections and their members."""

    def create(self, name: str, user_id: str, parent_id: Optional[str] = None) -> ModelCollection:
        """Create a new model collection owned by the given user, optionally nested."""
        collection_id = generate_ulid()
        now = datetime.now()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO model_collections (id, name, user_id, parent_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (collection_id, name, user_id, parent_id, now.isoformat()))

        return ModelCollection(
            id=collection_id,
            name=name,
            user_id=user_id,
            parent_id=parent_id,
            created_at=now,
            item_count=0
        )

    def get_by_id(self, collection_id: str, user_id: Optional[str] = None) -> Optional[ModelCollection]:
        """Get a model collection by ID, optionally scoped to an owner."""
        query = """
            SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at,
                   COUNT(mcm.model_id) as item_count
            FROM model_collections c
            LEFT JOIN model_collection_members mcm ON c.id = mcm.collection_id
            WHERE c.id = ?
        """
        params: List = [collection_id]

        if user_id is not None:
            query += " AND c.user_id = ?"
            params.append(user_id)

        query += " GROUP BY c.id"

        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return ModelCollection.from_row(row) if row else None

    def list(self, user_id: str) -> List[ModelCollection]:
        """List all model collections owned by the user, each with a model count."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at,
                       COUNT(mcm.model_id) as item_count
                FROM model_collections c
                LEFT JOIN model_collection_members mcm ON c.id = mcm.collection_id
                WHERE c.user_id = ?
                GROUP BY c.id
                ORDER BY c.name ASC
            """, (user_id,))
            return [ModelCollection.from_row(row) for row in cursor.fetchall()]

    def rename(self, collection_id: str, name: str, user_id: str) -> bool:
        """Rename a model collection owned by the user."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE model_collections SET name = ?
                WHERE id = ? AND user_id = ?
            """, (name, collection_id, user_id))
            return cursor.rowcount > 0

    def _creates_cycle(self, cursor, collection_id: str, new_parent_id: Optional[str]) -> bool:
        """Return True if reparenting collection under new_parent would form a cycle.

        A cycle occurs if new_parent is the collection itself or one of its
        descendants. We detect it by walking UP from new_parent toward the root
        and checking whether we pass through collection_id.
        """
        if new_parent_id is None:
            return False
        current = new_parent_id
        seen = set()
        while current is not None:
            if current == collection_id:
                return True
            if current in seen:  # guard against pre-existing corruption
                break
            seen.add(current)
            cursor.execute("SELECT parent_id FROM model_collections WHERE id = ?", (current,))
            row = cursor.fetchone()
            current = row['parent_id'] if row else None
        return False

    def move(self, collection_id: str, new_parent_id: Optional[str], user_id: str) -> bool:
        """Reparent a model collection (owned by user). new_parent_id None = move to root.

        Raises:
            ValueError: If the move would create a cycle.
        """
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id):
                return False
            if self._creates_cycle(cursor, collection_id, new_parent_id):
                raise ValueError("Cannot move a collection into itself or one of its subfolders")
            cursor.execute(
                "UPDATE model_collections SET parent_id = ? WHERE id = ? AND user_id = ?",
                (new_parent_id, collection_id, user_id)
            )
            return cursor.rowcount > 0

    def delete(self, collection_id: str, user_id: str) -> bool:
        """Delete a model collection owned by the user (cascades members and subfolders)."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM model_collections WHERE id = ? AND user_id = ?
            """, (collection_id, user_id))
            return cursor.rowcount > 0

    def _owns(self, cursor, collection_id: str, user_id: str) -> bool:
        """Return True if the collection exists and belongs to the user."""
        cursor.execute(
            "SELECT 1 FROM model_collections WHERE id = ? AND user_id = ?",
            (collection_id, user_id)
        )
        return cursor.fetchone() is not None

    def add_members(self, collection_id: str, model_ids: List[str], user_id: str) -> int:
        """
        Add models to a collection (owned by user), ignoring duplicates.

        Returns the number of memberships newly inserted. Returns 0 if the
        collection does not exist or is not owned by the user.
        """
        if not model_ids:
            return 0

        added = 0
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id):
                return 0

            for model_id in model_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO model_collection_members (collection_id, model_id, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (collection_id, model_id))
                added += cursor.rowcount

        return added

    def remove_members(self, collection_id: str, model_ids: List[str]) -> int:
        """Remove models from a collection. Returns rows removed."""
        if not model_ids:
            return 0

        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(model_ids))
            cursor.execute(f"""
                DELETE FROM model_collection_members
                WHERE collection_id = ? AND model_id IN ({placeholders})
            """, (collection_id, *model_ids))
            return cursor.rowcount

    def get_for_model(self, model_id: str) -> List[ModelCollection]:
        """List collections that contain the given model."""
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at
                FROM model_collections c
                JOIN model_collection_members mcm ON c.id = mcm.collection_id
                WHERE mcm.model_id = ?
                ORDER BY c.name ASC
            """, (model_id,))
            return [ModelCollection.from_row(row) for row in cursor.fetchall()]


# Global repository instance - for backward compatibility only.
# Prefer using the DI-injected ModelCollectionRepository instead.
model_collection_repo = ModelCollectionRepository()
