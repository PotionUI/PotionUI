"""
Library Repository

The filtered read side of the library: the `uploads` table (migration 087)
queried the way the history page queries generations - by media type, tags,
collection membership and free text, paginated.

Row lifecycle (create / get / delete) stays with `UploadRepository`; this
repository never writes. Every query takes `user_id` as its first filter, not
as an option: a library is private, and there is no listing that spans owners.

The list path is deliberately two statements (page + count) with no per-row
follow-up. Tags for a page are fetched in one batch by
`TagRepository.get_upload_tags_bulk`, so a page of 20 costs the same number of
queries as a page of 200.
"""

import logging
from typing import List, Optional, Tuple

from src.features.media.records import Upload

logger = logging.getLogger(__name__)


class LibraryRepository:
    """Filtered, user-scoped queries over library resources."""

    def _filters(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Tuple[str, list]:
        """Build the shared WHERE clause so list and count can never disagree."""
        # Excludes derived artifacts (e.g. inpainting masks, migration 120) -
        # the Library is a browsable collection, not every row `uploads` holds.
        clauses = ["u.user_id = ?", "u.purpose = 'user_upload'"]
        params: list = [user_id]

        if media_type:
            clauses.append("u.media_type = ?")
            params.append(media_type)

        if search:
            clauses.append("LOWER(COALESCE(u.original_filename, '')) LIKE LOWER(?)")
            params.append(f"%{search}%")

        if collection_id:
            clauses.append(
                "u.id IN (SELECT upload_id FROM collection_uploads WHERE collection_id = ?)"
            )
            params.append(collection_id)

        if tag_ids:
            # ALL of the given tags must be present, matching the history
            # tag filter (`TagRepository.get_generations_by_tags`).
            placeholders = ','.join('?' * len(tag_ids))
            clauses.append(f"""
                u.id IN (
                    SELECT upload_id FROM upload_tags
                    WHERE tag_id IN ({placeholders})
                    GROUP BY upload_id
                    HAVING COUNT(DISTINCT tag_id) = ?
                )
            """)
            params.extend(tag_ids)
            params.append(len(tag_ids))

        return " AND ".join(clauses), params

    def list_items(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Upload]:
        """One page of the user's library, newest first."""
        where, params = self._filters(user_id, media_type, tag_ids, collection_id, search)

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT u.* FROM uploads u
                WHERE {where}
                ORDER BY u.created_at DESC, u.id DESC
                LIMIT ? OFFSET ?
            """, (*params, limit, offset))
            return [Upload.from_row(row) for row in cursor.fetchall()]

    def count_items(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        tag_ids: Optional[List[str]] = None,
        collection_id: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """Total rows matching the same filters `list_items` applies."""
        where, params = self._filters(user_id, media_type, tag_ids, collection_id, search)

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) as count FROM uploads u WHERE {where}", params)
            row = cursor.fetchone()
            return row['count'] if row else 0

    def media_type_counts(self, user_id: str) -> dict:
        """How many items of each media type the user's library holds."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                SELECT media_type, COUNT(*) as count FROM uploads
                WHERE user_id = ? AND purpose = 'user_upload'
                GROUP BY media_type
            """, (user_id,))
            return {row['media_type']: row['count'] for row in cursor.fetchall()}
