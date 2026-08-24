"""
Migration 057: Chat modes
- Renames chat_sessions.session_type to mode and collapses legacy values
  ('segments', 'generation') into the single builtin 'generation' mode.
- Adds chat_sessions.title_generated for LLM-generated session titles
  (existing named sessions are marked generated so they are not retro-titled).
- Rebuilds the user index to cover (user_id, mode).
- Adds llm_prompt_styles.mode (NULL = global style).
- Adds enhancement_feedback.mode for mode-scoped learning.
"""

from src.platform.database.database import db


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    """Apply chat-mode schema changes."""
    with db.get_cursor() as cursor:
        if not _column_exists(cursor, "chat_sessions", "mode"):
            cursor.execute("ALTER TABLE chat_sessions RENAME COLUMN session_type TO mode")
            cursor.execute("UPDATE chat_sessions SET mode = 'generation'")
            print("Migration 057: renamed chat_sessions.session_type to mode")

        if not _column_exists(cursor, "chat_sessions", "title_generated"):
            cursor.execute(
                "ALTER TABLE chat_sessions ADD COLUMN title_generated INTEGER NOT NULL DEFAULT 0"
            )
            cursor.execute("UPDATE chat_sessions SET title_generated = 1 WHERE name IS NOT NULL")
            print("Migration 057: added chat_sessions.title_generated")

        cursor.execute("DROP INDEX IF EXISTS idx_chat_sessions_user")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, mode)"
        )

        if not _column_exists(cursor, "llm_prompt_styles", "mode"):
            cursor.execute("ALTER TABLE llm_prompt_styles ADD COLUMN mode TEXT")
            print("Migration 057: added llm_prompt_styles.mode")

        if not _column_exists(cursor, "enhancement_feedback", "mode"):
            cursor.execute("ALTER TABLE enhancement_feedback ADD COLUMN mode TEXT")
            print("Migration 057: added enhancement_feedback.mode")


def down():
    """Revert chat-mode schema changes."""
    with db.get_cursor() as cursor:
        # SQLite supports RENAME COLUMN (3.25+); dropping columns requires a
        # table rebuild for chat_sessions.title_generated.
        if _column_exists(cursor, "chat_sessions", "mode"):
            cursor.execute("ALTER TABLE chat_sessions RENAME COLUMN mode TO session_type")

        if _column_exists(cursor, "chat_sessions", "title_generated"):
            cursor.execute("""
                CREATE TABLE chat_sessions_new (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    session_type TEXT NOT NULL DEFAULT 'segments',
                    name TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    llm_config_id TEXT,
                    style_id TEXT,
                    original_text TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP
                )
            """)
            cursor.execute("""
                INSERT INTO chat_sessions_new
                (id, user_id, session_type, name, status, llm_config_id, style_id,
                 original_text, metadata, created_at, updated_at, closed_at)
                SELECT id, user_id, session_type, name, status, llm_config_id, style_id,
                       original_text, metadata, created_at, updated_at, closed_at
                FROM chat_sessions
            """)
            cursor.execute("DROP TABLE chat_sessions")
            cursor.execute("ALTER TABLE chat_sessions_new RENAME TO chat_sessions")

        cursor.execute("DROP INDEX IF EXISTS idx_chat_sessions_user")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, session_type)"
        )
        # llm_prompt_styles.mode / enhancement_feedback.mode are additive and harmless;
        # left in place on downgrade.
        print("Migration 057: reverted chat mode schema changes")
