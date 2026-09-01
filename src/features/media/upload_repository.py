"""
Repository for the `uploads` table (migration 087).

Every read is scoped to `user_id` - there is no `get_by_id`/`get_by_filename`
without an owner to check against, because the whole point of this table is
that a user's upload library only ever shows (or deletes) their own files.
"""

from typing import List, Optional

from src.features.media.records import Upload
from src.platform.util.ids import generate_ulid


class UploadRepository:
    """Data access for user-owned media-loader uploads."""

    def create(self, upload: Upload) -> Upload:
        """Record a freshly-saved upload's ownership and metadata."""
        if not upload.id:
            upload.id = generate_ulid()

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO uploads (
                    id, user_id, filename, original_filename, media_type, mime_type,
                    width, height, duration_seconds, fps, file_size, purpose,
                    thumbnail_small, thumbnail_medium, thumbnail_large
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload.id,
                upload.user_id,
                upload.filename,
                upload.original_filename,
                upload.media_type,
                upload.mime_type,
                upload.width,
                upload.height,
                upload.duration_seconds,
                upload.fps,
                upload.file_size,
                upload.purpose,
                upload.thumbnail_small,
                upload.thumbnail_medium,
                upload.thumbnail_large,
            ))

            cursor.execute("SELECT * FROM uploads WHERE id = ?", (upload.id,))
            row = cursor.fetchone()
            return Upload.from_row(row) if row else None

    def get_by_id(self, upload_id: str, user_id: str) -> Optional[Upload]:
        """Look up one upload by its row id, scoped to its owner.

        Same contract as `get_by_filename`: None covers both "no such id" and
        "not yours", so a caller cannot tell the two apart.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM uploads WHERE id = ? AND user_id = ?",
                (upload_id, user_id)
            )
            row = cursor.fetchone()
            return Upload.from_row(row) if row else None

    def get_by_filename(self, filename: str, user_id: str) -> Optional[Upload]:
        """Look up one upload, scoped to its owner.

        Returns None both when the filename doesn't exist and when it
        belongs to a different user - callers must not distinguish the two
        (GenerationPolicy precedent: 404, never 403).
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM uploads WHERE filename = ? AND user_id = ?",
                (filename, user_id)
            )
            row = cursor.fetchone()
            return Upload.from_row(row) if row else None

    def get_by_filename_unscoped(self, filename: str) -> Optional[Upload]:
        """Look up one upload by filename with no owner check.

        For `GET /api/media/uploads/{filename}` only - that route is already
        unauthenticated (an `<img src>` cannot attach a bearer token), so the
        on-disk uuid filename is already the whole access boundary; resolving
        which thumbnail sizes exist for it adds nothing an unscoped read of
        the bytes themselves doesn't already expose.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM uploads WHERE filename = ?", (filename,))
            row = cursor.fetchone()
            return Upload.from_row(row) if row else None

    def set_thumbnail_paths(
        self,
        upload_id: str,
        thumbnail_small: Optional[str],
        thumbnail_medium: Optional[str],
        thumbnail_large: Optional[str],
    ) -> bool:
        """Set thumbnail paths on one upload row, once they're ready.

        Only the asynchronous video path needs this - image thumbnails are
        generated before the row is first created and go in with `create()`.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE uploads
                   SET thumbnail_small = ?, thumbnail_medium = ?, thumbnail_large = ?
                 WHERE id = ?
                """,
                (thumbnail_small, thumbnail_medium, thumbnail_large, upload_id),
            )
            return cursor.rowcount > 0

    def list_for_user(
        self,
        user_id: str,
        media_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Upload]:
        """List a user's uploads, newest first."""
        query = "SELECT * FROM uploads WHERE user_id = ?"
        params: list = [user_id]

        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)

        query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            return [Upload.from_row(row) for row in cursor.fetchall()]

    def count_for_user(self, user_id: str, media_type: Optional[str] = None) -> int:
        """Total uploads for a user, honoring the same filter as list_for_user."""
        query = "SELECT COUNT(*) as count FROM uploads WHERE user_id = ?"
        params: list = [user_id]

        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)

        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return row['count'] if row else 0

    def update_file(
        self,
        upload_id: str,
        user_id: str,
        filename: str,
        mime_type: Optional[str],
        width: Optional[int],
        height: Optional[int],
        duration_seconds: Optional[float],
        fps: Optional[float],
        file_size: Optional[int],
    ) -> Optional[Upload]:
        """Point an existing upload row at a different file, scoped to its owner.

        Everything that describes the bytes is rewritten together - a row left
        with the old width beside a new filename is worse than no metadata at
        all. `media_type` and `original_filename` are not among them: the row
        keeps the identity (and so the tags and collection memberships) it had,
        and only the file behind it changes.

        Returns the updated row, or None if no row with this id belongs to this
        user - callers must not distinguish "no such id" from "not yours".
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                UPDATE uploads
                   SET filename = ?,
                       mime_type = ?,
                       width = ?,
                       height = ?,
                       duration_seconds = ?,
                       fps = ?,
                       file_size = ?
                 WHERE id = ? AND user_id = ?
            """, (
                filename,
                mime_type,
                width,
                height,
                duration_seconds,
                fps,
                file_size,
                upload_id,
                user_id,
            ))
            if cursor.rowcount == 0:
                return None

            cursor.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,))
            row = cursor.fetchone()
            return Upload.from_row(row) if row else None

    def delete(self, filename: str, user_id: str) -> bool:
        """Delete one upload row, scoped to its owner. Returns whether a row was removed."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM uploads WHERE filename = ? AND user_id = ?",
                (filename, user_id)
            )
            return cursor.rowcount > 0
