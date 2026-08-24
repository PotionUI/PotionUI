"""
Migration 128: thumbnail columns on `uploads`.

`files` (generation output) has carried `thumbnail_small/medium/large` since
migration 025 - a Library item (an `uploads` row) never had anywhere to put
the same paths, so every Library upload rendered its grid tile from the
full-size original. This gives `uploads` the same three columns, same
semantics: a path relative to the row's own storage key, or NULL when no
thumbnail exists yet (video thumbnails are generated asynchronously; existing
rows predate this column entirely).
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        for column in ("thumbnail_small", "thumbnail_medium", "thumbnail_large"):
            if _has_column(cursor, "uploads", column):
                continue
            cursor.execute(f"ALTER TABLE uploads ADD COLUMN {column} TEXT")
        print("Migration 128: added uploads.thumbnail_small/medium/large")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "uploads", "thumbnail_small"):
            print("Migration 128: uploads thumbnail columns absent, nothing to drop")
            return
        for column in ("thumbnail_small", "thumbnail_medium", "thumbnail_large"):
            cursor.execute(f"ALTER TABLE uploads DROP COLUMN {column}")
        print("Migration 128: dropped uploads thumbnail columns")
