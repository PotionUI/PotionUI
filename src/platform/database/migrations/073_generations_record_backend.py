"""
Record which backend produced each generation.

`generations` stored `preset_id` but nothing about where the work ran. A preset
declares an *engine*; an engine can have several *backends* (see docs/backends.md),
so with two ComfyUI servers configured there was no way to tell which one produced
an image — and no way to debug a model that exists on one backend but not another.

The column is nullable: rows created before this migration have no answer, and
imported/uploaded generations (history_manager) legitimately have no backend.

No foreign key to `backends(id)`: a backend can be deleted while its generations
remain, and history must survive that. The id is kept as a plain reference.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "generations", "backend_id"):
            print("Migration 073: generations.backend_id already present, skipping")
            return

        cursor.execute("ALTER TABLE generations ADD COLUMN backend_id TEXT")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_generations_backend_id "
            "ON generations(backend_id)"
        )
        print("Migration 073: added generations.backend_id")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "backend_id"):
            print("Migration 073: generations.backend_id absent, nothing to drop")
            return

        cursor.execute("DROP INDEX IF EXISTS idx_generations_backend_id")
        # SQLite gained DROP COLUMN in 3.35 (2021); the runtime is well past that.
        cursor.execute("ALTER TABLE generations DROP COLUMN backend_id")
        print("Migration 073: dropped generations.backend_id")
