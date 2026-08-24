"""
Migration 121: soft "unavailable" marking for models whose file went missing.

Relocating the models directory (see `src.features.models.location`) points the
`./models/<type>` symlinks at a different external directory; a model row
indexed under the old location may have no file at its `file_path` until the
admin switches back. Before this migration the indexer's cleanup pass hard-
deleted such rows (`ModelRepository.delete`), which threw away their tags,
ratings and user assignments - dropping the row was indistinguishable from the
user deleting the model on purpose. `is_available` records "the file wasn't
found on disk at the last scan" without discarding anything; the indexer
revives a row (sets it back to available) the next time a scan finds its file.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "models", "is_available"):
            print("Migration 121: models.is_available already present, skipping")
            return

        cursor.execute("ALTER TABLE models ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1")
        cursor.execute("ALTER TABLE models ADD COLUMN unavailable_at TIMESTAMP DEFAULT NULL")
        print("Migration 121: added models.is_available + models.unavailable_at")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "models", "is_available"):
            print("Migration 121: models.is_available absent, nothing to drop")
            return
        cursor.execute("ALTER TABLE models DROP COLUMN is_available")
        cursor.execute("ALTER TABLE models DROP COLUMN unavailable_at")
        print("Migration 121: dropped models.is_available + models.unavailable_at")
