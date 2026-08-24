"""Inspirations domain repository.

Read-heavy by design (house rule: reads go route -> repository directly,
managers are for mutations). Every list/detail query joins `users` for author
display info and computes `comment_count`/`save_count`/`saved_by_me` inline,
so a caller never needs a second round trip to render a feed card.
"""

import json
import logging
from typing import List, Optional, Tuple

from src.platform.database import db
from src.platform.util.ids import generate_ulid

from src.features.inspirations.records import (
    Inspiration,
    InspirationComment,
    InspirationCollection,
)

logger = logging.getLogger(__name__)

# Shared by every query that needs the author + counts + this-viewer's-save
# state attached to an inspiration row. `?` #1 is always the viewer id (for
# `saved_by_me`); callers append their own filter params after it.
_FEED_SELECT = """
    SELECT i.*,
           u.username AS author_username,
           u.avatar_filename AS author_avatar_filename,
           (SELECT COUNT(*) FROM inspiration_comments c WHERE c.inspiration_id = i.id) AS comment_count,
           (SELECT COUNT(*) FROM inspiration_saves sv WHERE sv.inspiration_id = i.id) AS save_count,
           EXISTS(
               SELECT 1 FROM inspiration_saves sv2
               WHERE sv2.inspiration_id = i.id AND sv2.user_id = ?
           ) AS saved_by_me
    FROM inspirations i
    JOIN users u ON u.id = i.user_id
"""


class InspirationRepository:

    # ========== Inspirations: read ==========

    def get_by_id(self, inspiration_id: str, viewer_id: Optional[str] = None) -> Optional[Inspiration]:
        """One inspiration by id, with author/counts/saved_by_me attached.

        `viewer_id` only affects `saved_by_me` - the row itself is public to
        any viewer (feature-flag gating happens above this layer).
        """
        with db.get_cursor() as cursor:
            cursor.execute(
                _FEED_SELECT + " WHERE i.id = ?",
                (viewer_id or "", inspiration_id),
            )
            row = cursor.fetchone()
            return Inspiration.from_row(row) if row else None

    def list_feed(
        self,
        viewer_id: Optional[str],
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        collection_id: Optional[str] = None,
        author_id: Optional[str] = None,
        saved: Optional[bool] = None,
    ) -> Tuple[List[Inspiration], int]:
        """One filtered, newest-first page of the public feed, plus its total."""
        where = []
        params: List = []

        if query:
            like = f"%{query}%"
            where.append("(i.title LIKE ? OR i.description LIKE ? OR u.username LIKE ?)")
            params.extend([like, like, like])

        if author_id:
            where.append("i.user_id = ?")
            params.append(author_id)

        if collection_id:
            # Scoped to the viewer's own collection - a foreign or unknown
            # collection_id yields an empty page rather than an error.
            where.append("""
                i.id IN (
                    SELECT ci.inspiration_id FROM inspiration_collection_items ci
                    JOIN inspiration_collections ic ON ic.id = ci.collection_id
                    WHERE ci.collection_id = ? AND ic.user_id = ?
                )
            """)
            params.extend([collection_id, viewer_id or ""])

        if saved:
            where.append("i.id IN (SELECT inspiration_id FROM inspiration_saves WHERE user_id = ?)")
            params.append(viewer_id or "")

        where_clause = f"WHERE {' AND '.join(where)}" if where else ""

        with db.get_cursor() as cursor:
            cursor.execute(
                _FEED_SELECT + f" {where_clause} ORDER BY i.created_at DESC LIMIT ? OFFSET ?",
                (viewer_id or "", *params, limit, offset),
            )
            items = [Inspiration.from_row(row) for row in cursor.fetchall()]

            cursor.execute(
                f"SELECT COUNT(*) AS n FROM inspirations i JOIN users u ON u.id = i.user_id {where_clause}",
                tuple(params),
            )
            total = cursor.fetchone()["n"]

        return items, total

    # ========== Inspirations: write ==========

    def create(self, inspiration: Inspiration) -> Inspiration:
        if not inspiration.id:
            inspiration.id = generate_ulid()

        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO inspirations (
                    id, user_id, title, description, media, params_snapshot,
                    preset_id, preset_name, technique, source_generation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                inspiration.id,
                inspiration.user_id,
                inspiration.title,
                inspiration.description,
                json.dumps(inspiration.media),
                json.dumps(inspiration.params_snapshot),
                inspiration.preset_id,
                inspiration.preset_name,
                inspiration.technique,
                inspiration.source_generation_id,
            ))

        created = self.get_by_id(inspiration.id, viewer_id=inspiration.user_id)
        if created is None:
            raise RuntimeError(f"Failed to read back created inspiration {inspiration.id}")
        return created

    def delete(self, inspiration_id: str) -> bool:
        """Delete the row (comments/collection items/saves cascade)."""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM inspirations WHERE id = ?", (inspiration_id,))
            return cursor.rowcount > 0

    # ========== Comments ==========

    def create_comment(self, inspiration_id: str, user_id: str, body: str) -> InspirationComment:
        comment_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO inspiration_comments (id, inspiration_id, user_id, body)
                VALUES (?, ?, ?, ?)
            """, (comment_id, inspiration_id, user_id, body))
        comment = self.get_comment(comment_id)
        if comment is None:
            raise RuntimeError(f"Failed to read back created comment {comment_id}")
        return comment

    def get_comment(self, comment_id: str) -> Optional[InspirationComment]:
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.*, u.username AS author_username, u.avatar_filename AS author_avatar_filename
                FROM inspiration_comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.id = ?
            """, (comment_id,))
            row = cursor.fetchone()
            return InspirationComment.from_row(row) if row else None

    def list_comments(self, inspiration_id: str) -> List[InspirationComment]:
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT c.*, u.username AS author_username, u.avatar_filename AS author_avatar_filename
                FROM inspiration_comments c
                JOIN users u ON u.id = c.user_id
                WHERE c.inspiration_id = ?
                ORDER BY c.created_at ASC
            """, (inspiration_id,))
            return [InspirationComment.from_row(row) for row in cursor.fetchall()]

    def delete_comment(self, comment_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM inspiration_comments WHERE id = ?", (comment_id,))
            return cursor.rowcount > 0

    # ========== Saves ==========

    def create_save(self, user_id: str, inspiration_id: str) -> None:
        """Idempotent: saving an already-saved inspiration is a no-op."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO inspiration_saves (user_id, inspiration_id) VALUES (?, ?)",
                (user_id, inspiration_id),
            )

    def delete_save(self, user_id: str, inspiration_id: str) -> None:
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM inspiration_saves WHERE user_id = ? AND inspiration_id = ?",
                (user_id, inspiration_id),
            )

    def count_saves(self, inspiration_id: str) -> int:
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM inspiration_saves WHERE inspiration_id = ?",
                (inspiration_id,),
            )
            return cursor.fetchone()["n"]

    # ========== Collections ==========

    def create_collection(self, user_id: str, name: str, parent_id: Optional[str] = None) -> InspirationCollection:
        collection_id = generate_ulid()
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO inspiration_collections (id, user_id, name, parent_id) VALUES (?, ?, ?, ?)",
                (collection_id, user_id, name, parent_id),
            )
        collection = self.get_collection(collection_id, user_id)
        if collection is None:
            raise RuntimeError(f"Failed to read back created collection {collection_id}")
        return collection

    def get_collection(self, collection_id: str, user_id: str) -> Optional[InspirationCollection]:
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT ic.*,
                       (SELECT COUNT(*) FROM inspiration_collection_items ci
                        WHERE ci.collection_id = ic.id) AS item_count
                FROM inspiration_collections ic
                WHERE ic.id = ? AND ic.user_id = ?
            """, (collection_id, user_id))
            row = cursor.fetchone()
            return InspirationCollection.from_row(row) if row else None

    def list_collections(self, user_id: str) -> List[InspirationCollection]:
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT ic.*,
                       (SELECT COUNT(*) FROM inspiration_collection_items ci
                        WHERE ci.collection_id = ic.id) AS item_count
                FROM inspiration_collections ic
                WHERE ic.user_id = ?
                ORDER BY ic.name ASC
            """, (user_id,))
            return [InspirationCollection.from_row(row) for row in cursor.fetchall()]

    def rename_collection(self, collection_id: str, user_id: str, name: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE inspiration_collections SET name = ? WHERE id = ? AND user_id = ?",
                (name, collection_id, user_id),
            )
            return cursor.rowcount > 0

    def move_collection(self, collection_id: str, user_id: str, new_parent_id: Optional[str]) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE inspiration_collections SET parent_id = ? WHERE id = ? AND user_id = ?",
                (new_parent_id, collection_id, user_id),
            )
            return cursor.rowcount > 0

    def creates_cycle(self, collection_id: str, new_parent_id: Optional[str]) -> bool:
        """True if reparenting `collection_id` under `new_parent_id` would form a
        cycle - detected by walking up from the new parent toward the root."""
        if new_parent_id is None:
            return False
        with db.get_cursor() as cursor:
            current = new_parent_id
            seen = set()
            while current is not None:
                if current == collection_id:
                    return True
                if current in seen:
                    break
                seen.add(current)
                cursor.execute(
                    "SELECT parent_id FROM inspiration_collections WHERE id = ?", (current,)
                )
                row = cursor.fetchone()
                current = row["parent_id"] if row else None
        return False

    def delete_collection(self, collection_id: str, user_id: str) -> bool:
        """Delete a collection (memberships cascade; inspirations themselves untouched)."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM inspiration_collections WHERE id = ? AND user_id = ?",
                (collection_id, user_id),
            )
            return cursor.rowcount > 0

    def add_item(self, collection_id: str, inspiration_id: str) -> None:
        """Idempotent: adding an already-present inspiration is a no-op."""
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO inspiration_collection_items (collection_id, inspiration_id) "
                "VALUES (?, ?)",
                (collection_id, inspiration_id),
            )

    def remove_item(self, collection_id: str, inspiration_id: str) -> bool:
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM inspiration_collection_items WHERE collection_id = ? AND inspiration_id = ?",
                (collection_id, inspiration_id),
            )
            return cursor.rowcount > 0
