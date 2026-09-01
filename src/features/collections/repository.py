"""
Collection Repository

Handles database operations for collections (named, user-owned virtual groupings
of media) and the junction tables that fill them: collection_generations for
history generations, collection_uploads for library uploads, and
collection_prompts for saved prompts. Every collection has a `scope`
('history' | 'library' | 'prompts', migrations 137 - see
`src.features.collections.dto.ALLOWED_SCOPES` for the open enumeration) and
every query here filters by it - collections in different scopes never mix,
even though all three still live in the one `collections` table and share the
`item_count` expression below (in practice only one junction is ever non-empty
for a given row, since scope determines which one a collection can accept,
but the expression stays a sum of all three for safety).
"""
from typing import List, Optional
from datetime import datetime
from src.features.collections.records import Collection
from src.platform.util.ids import generate_ulid
import logging

logger = logging.getLogger(__name__)

# A collection's size is the sum of its generations, uploads, and prompts.
# All three junctions are LEFT JOINed at once, so COUNT(DISTINCT ...) - not
# COUNT(...) - is required: the join multiplies rows, and a plain count would
# report generations x uploads x prompts instead of their sum.
_ITEM_COUNT_JOINS = """
            LEFT JOIN collection_generations cg ON c.id = cg.collection_id
            LEFT JOIN collection_uploads cu ON c.id = cu.collection_id
            LEFT JOIN collection_prompts cp ON c.id = cp.collection_id
"""
_ITEM_COUNT_EXPR = (
    "COUNT(DISTINCT cg.generation_id) + COUNT(DISTINCT cu.upload_id) + COUNT(DISTINCT cp.prompt_id)"
)


class CollectionRepository:
    """Repository for managing collections and their members."""

    def create(self, name: str, user_id: str, scope: str, parent_id: Optional[str] = None) -> Collection:
        """Create a new collection owned by the given user, optionally nested.

        A collection's tree is scoped: 'history' folders never mix with
        'library' folders (see migration 137).
        """
        collection_id = generate_ulid()
        now = datetime.now()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO collections (id, name, user_id, parent_id, created_at, scope)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (collection_id, name, user_id, parent_id, now.isoformat(), scope))

        return Collection(
            id=collection_id,
            name=name,
            user_id=user_id,
            scope=scope,
            parent_id=parent_id,
            created_at=now,
            item_count=0
        )

    def get_by_id(self, collection_id: str, scope: str, user_id: Optional[str] = None) -> Optional[Collection]:
        """Get a collection by ID within its scope, optionally scoped to an owner too."""
        query = f"""
            SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at, c.scope,
                   {_ITEM_COUNT_EXPR} as item_count
            FROM collections c
            {_ITEM_COUNT_JOINS}
            WHERE c.id = ? AND c.scope = ?
        """
        params: List = [collection_id, scope]

        if user_id is not None:
            query += " AND c.user_id = ?"
            params.append(user_id)

        query += " GROUP BY c.id"

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return Collection.from_row(row) if row else None

    def list(self, user_id: str, scope: str) -> List[Collection]:
        """List all of the user's collections within a scope, each with its item count."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at, c.scope,
                       {_ITEM_COUNT_EXPR} as item_count
                FROM collections c
                {_ITEM_COUNT_JOINS}
                WHERE c.user_id = ? AND c.scope = ?
                GROUP BY c.id
                ORDER BY c.name ASC
            """, (user_id, scope))
            return [Collection.from_row(row) for row in cursor.fetchall()]

    def rename(self, collection_id: str, name: str, user_id: str, scope: str) -> bool:
        """Rename a collection owned by the user, within its scope."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE collections SET name = ?
                WHERE id = ? AND user_id = ? AND scope = ?
            """, (name, collection_id, user_id, scope))
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
            cursor.execute("SELECT parent_id FROM collections WHERE id = ?", (current,))
            row = cursor.fetchone()
            current = row['parent_id'] if row else None
        return False

    def move(self, collection_id: str, new_parent_id: Optional[str], user_id: str, scope: str) -> bool:
        """Reparent a collection (owned by user, within scope). new_parent_id None = move to root.

        Raises:
            ValueError: If the move would create a cycle.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id, scope):
                return False
            if self._creates_cycle(cursor, collection_id, new_parent_id):
                raise ValueError("Cannot move a collection into itself or one of its subfolders")
            cursor.execute(
                "UPDATE collections SET parent_id = ? WHERE id = ? AND user_id = ? AND scope = ?",
                (new_parent_id, collection_id, user_id, scope)
            )
            return cursor.rowcount > 0

    def delete(self, collection_id: str, user_id: str, scope: str) -> bool:
        """Delete a collection owned by the user, within scope (cascades members and subfolders)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM collections WHERE id = ? AND user_id = ? AND scope = ?
            """, (collection_id, user_id, scope))
            return cursor.rowcount > 0

    def _owns(self, cursor, collection_id: str, user_id: str, scope: str) -> bool:
        """Return True if the collection exists, belongs to the user, and matches scope."""
        cursor.execute(
            "SELECT 1 FROM collections WHERE id = ? AND user_id = ? AND scope = ?",
            (collection_id, user_id, scope)
        )
        return cursor.fetchone() is not None

    def add_members(self, collection_id: str, generation_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add generations to a collection (owned by user, within scope), ignoring duplicates.

        Returns the number of memberships newly inserted. Returns 0 if the
        collection does not exist, is not owned by the user, or is not in scope.
        """
        if not generation_ids:
            return 0

        added = 0
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id, scope):
                return 0

            for generation_id in generation_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO collection_generations (collection_id, generation_id, created_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (collection_id, generation_id))
                added += cursor.rowcount

        return added

    def remove_members(self, collection_id: str, generation_ids: List[str]) -> int:
        """Remove generations from a collection. Returns rows removed."""
        if not generation_ids:
            return 0

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(generation_ids))
            cursor.execute(f"""
                DELETE FROM collection_generations
                WHERE collection_id = ? AND generation_id IN ({placeholders})
            """, (collection_id, *generation_ids))
            return cursor.rowcount

    def add_upload_members(self, collection_id: str, upload_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add library uploads to a collection (owned by user, within scope), ignoring duplicates.

        The INSERT selects from `uploads` filtered by owner, so an id belonging
        to someone else inserts nothing - a caller cannot file another user's
        upload into their own folder, and cannot tell that id apart from one
        that does not exist.

        Returns the number of memberships newly inserted. Returns 0 if the
        collection does not exist, is not owned by the user, or is not in scope.
        """
        if not upload_ids:
            return 0

        added = 0
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id, scope):
                return 0

            for upload_id in upload_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO collection_uploads (collection_id, upload_id, created_at)
                    SELECT ?, id, CURRENT_TIMESTAMP FROM uploads WHERE id = ? AND user_id = ?
                """, (collection_id, upload_id, user_id))
                added += cursor.rowcount

        return added

    def remove_upload_members(self, collection_id: str, upload_ids: List[str]) -> int:
        """Remove library uploads from a collection. Returns rows removed."""
        if not upload_ids:
            return 0

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(upload_ids))
            cursor.execute(f"""
                DELETE FROM collection_uploads
                WHERE collection_id = ? AND upload_id IN ({placeholders})
            """, (collection_id, *upload_ids))
            return cursor.rowcount

    def add_prompt_members(self, collection_id: str, prompt_ids: List[str], user_id: str, scope: str) -> int:
        """
        Add saved prompts to a collection (owned by user, within scope), ignoring duplicates.

        Mirrors add_upload_members: the INSERT selects from `prompts` filtered
        by owner, so an id belonging to someone else inserts nothing.

        Returns the number of memberships newly inserted. Returns 0 if the
        collection does not exist, is not owned by the user, or is not in scope.
        """
        if not prompt_ids:
            return 0

        added = 0
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if not self._owns(cursor, collection_id, user_id, scope):
                return 0

            for prompt_id in prompt_ids:
                cursor.execute("""
                    INSERT OR IGNORE INTO collection_prompts (collection_id, prompt_id, added_at)
                    SELECT ?, id, CURRENT_TIMESTAMP FROM prompts WHERE id = ? AND user_id = ?
                """, (collection_id, prompt_id, user_id))
                added += cursor.rowcount

        return added

    def remove_prompt_members(self, collection_id: str, prompt_ids: List[str]) -> int:
        """Remove saved prompts from a collection. Returns rows removed."""
        if not prompt_ids:
            return 0

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            placeholders = ','.join('?' * len(prompt_ids))
            cursor.execute(f"""
                DELETE FROM collection_prompts
                WHERE collection_id = ? AND prompt_id IN ({placeholders})
            """, (collection_id, *prompt_ids))
            return cursor.rowcount

    def get_for_prompt(self, prompt_id: str) -> List[Collection]:
        """List collections that contain the given saved prompt (always 'prompts' scope)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at, c.scope
                FROM collections c
                JOIN collection_prompts cp ON c.id = cp.collection_id
                WHERE cp.prompt_id = ?
                ORDER BY c.name ASC
            """, (prompt_id,))
            return [Collection.from_row(row) for row in cursor.fetchall()]

    def get_for_upload(self, upload_id: str) -> List[Collection]:
        """List collections that contain the given library upload (always 'library' scope)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at, c.scope
                FROM collections c
                JOIN collection_uploads cu ON c.id = cu.collection_id
                WHERE cu.upload_id = ?
                ORDER BY c.name ASC
            """, (upload_id,))
            return [Collection.from_row(row) for row in cursor.fetchall()]

    def get_for_generation(self, generation_id: str) -> List[Collection]:
        """List collections that contain the given generation (always 'history' scope)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.id, c.name, c.user_id, c.parent_id, c.created_at, c.scope
                FROM collections c
                JOIN collection_generations cg ON c.id = cg.collection_id
                WHERE cg.generation_id = ?
                ORDER BY c.name ASC
            """, (generation_id,))
            return [Collection.from_row(row) for row in cursor.fetchall()]


# Global repository instance - for backward compatibility only.
# Prefer using the DI-injected CollectionRepository instead.
collection_repo = CollectionRepository()
