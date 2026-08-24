"""
Migration 125: provenance link from a generation back to the library prompt
it was submitted from.

`source_prompt_id` is a bare TEXT column, not a foreign key: prompts are
deleted freely (bulk-delete, purge-by-model) and a generation's history must
survive that - a dangling id simply resolves to nothing when the Prompt
Library looks up "used in generations" for a prompt that no longer exists.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "source_prompt_id"):
            cursor.execute("ALTER TABLE generations ADD COLUMN source_prompt_id TEXT DEFAULT NULL")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_generations_source_prompt_id "
            "ON generations(source_prompt_id)"
        )
        print("Migration 125: added generations.source_prompt_id + index")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_generations_source_prompt_id")
        if _has_column(cursor, "generations", "source_prompt_id"):
            cursor.execute("ALTER TABLE generations DROP COLUMN source_prompt_id")
        print("Migration 125: dropped generations.source_prompt_id + index")
