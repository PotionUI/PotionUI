"""
Migration 082: Remove the LLM "styles" feature.

Styles (a suffix appended to the system prompt) were dropped: no plugin,
core caller, or UI surface uses them anymore. This drops the
llm_prompt_styles table (and its updated_at trigger) and the
chat_sessions.style_id column that referenced it.
"""

from src.platform.database.database import db


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _table_exists(cursor, table: str) -> bool:
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cursor.fetchone() is not None


def up():
    """Drop llm_prompt_styles and chat_sessions.style_id."""
    with db.get_cursor() as cursor:
        if _table_exists(cursor, "llm_prompt_styles"):
            cursor.execute("DROP TRIGGER IF EXISTS update_llm_prompt_styles_updated_at")
            cursor.execute("DROP TABLE llm_prompt_styles")
            print("Migration 082: dropped llm_prompt_styles")

        if _table_exists(cursor, "chat_sessions") and _column_exists(cursor, "chat_sessions", "style_id"):
            cursor.execute("ALTER TABLE chat_sessions DROP COLUMN style_id")
            print("Migration 082: dropped chat_sessions.style_id")


def down():
    """Recreate llm_prompt_styles and chat_sessions.style_id (empty)."""
    with db.get_cursor() as cursor:
        if _table_exists(cursor, "chat_sessions") and not _column_exists(cursor, "chat_sessions", "style_id"):
            cursor.execute("ALTER TABLE chat_sessions ADD COLUMN style_id TEXT")

        if not _table_exists(cursor, "llm_prompt_styles"):
            cursor.execute("""
                CREATE TABLE llm_prompt_styles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    suffix TEXT NOT NULL,
                    user_id TEXT,
                    mode TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TRIGGER update_llm_prompt_styles_updated_at
                AFTER UPDATE ON llm_prompt_styles
                FOR EACH ROW
                BEGIN
                    UPDATE llm_prompt_styles SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
                END
            """)
        print("Migration 082: reverted (style data is not restored)")
