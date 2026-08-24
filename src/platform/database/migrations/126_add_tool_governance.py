"""Migration 126: admin/user governance over LLM chat tools.

`tool_governance` holds one row per (llm_config_id, tool_name) an admin has
touched - governance is per LLM configuration, not global: the same tool can
be enabled in one config and disabled in another. A (config, tool) pair with
no row defaults to enabled + unlocked - zero behavior change until an admin
acts. `user_disabled_tools` is a per-user opt-out set that is NOT scoped to a
config - it follows the user across whichever config their session uses, and
is only overridden by that session's config `locked` row.
"""

from src.platform.database.database import db


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def up():
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_governance (
                llm_config_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (llm_config_id, tool_name)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_disabled_tools (
                user_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, tool_name)
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_disabled_tools_user "
            "ON user_disabled_tools(user_id)"
        )
        print("Migration 126: created tool_governance + user_disabled_tools")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_user_disabled_tools_user")
        if _table_exists(cursor, "user_disabled_tools"):
            cursor.execute("DROP TABLE user_disabled_tools")
        if _table_exists(cursor, "tool_governance"):
            cursor.execute("DROP TABLE tool_governance")
        print("Migration 126: dropped tool_governance + user_disabled_tools")
