"""Migration 132: repair `tool_governance` schema drift left by migration 126.

Migration 126 was applied to at least one live database while its `up()`
still created a *global* `tool_governance` table (`tool_name TEXT PRIMARY
KEY`, no `llm_config_id`) - an early version of the design that predates
per-LLM-config governance. The migration file was edited afterwards to the
per-config schema documented in its current docstring (`PRIMARY KEY
(llm_config_id, tool_name)`), but because `applied_migrations` already
recorded `126_add_tool_governance` as applied, the runner never re-executes
it and `CREATE TABLE IF NOT EXISTS` is a no-op against the old table. Every
caller (governance_repository.py, ChatContextBuilder, governance_routes.py)
is written against the new schema, so every query fails with
`no such column: llm_config_id`.

This migration is the sanctioned fix: never edit an already-applied
migration file (that change would never re-run on a database where it's
already marked applied) - instead land the schema change as its own
migration. It detects the pre-126.1 global shape and rebuilds the table to
the per-config shape, dropping any rows found under the old shape (a
`tool_governance` row is an admin override; its absence is a documented safe
default of enabled+unlocked, so there is nothing meaningful to backfill a
per-config key with) and logs how many were dropped for operator visibility.
"""

import logging

from src.platform.database.database import db

logger = logging.getLogger(__name__)


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if not _table_exists(cursor, "tool_governance"):
            # Never applied at all (fresh install, or 126 ran with the
            # current per-config schema already) - nothing to repair.
            print("Migration 132: tool_governance absent, nothing to repair")
            return

        if _has_column(cursor, "tool_governance", "llm_config_id"):
            print("Migration 132: tool_governance already per-config, nothing to repair")
            return

        cursor.execute("SELECT COUNT(*) FROM tool_governance")
        stale_rows = cursor.fetchone()[0]
        if stale_rows:
            logger.warning(
                "Migration 132: dropping %d tool_governance row(s) recorded under the "
                "pre-per-config schema (no llm_config_id to backfill them with)",
                stale_rows,
            )

        cursor.execute("ALTER TABLE tool_governance RENAME TO tool_governance_pre132")
        cursor.execute("""
            CREATE TABLE tool_governance (
                llm_config_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                locked INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (llm_config_id, tool_name)
            )
        """)
        cursor.execute("DROP TABLE tool_governance_pre132")
        print(
            f"Migration 132: rebuilt tool_governance to the per-config schema "
            f"({stale_rows} stale row(s) dropped)"
        )


def down():
    # The pre-132 global schema is gone for good (126 is where it's meant to
    # live); nothing to revert to.
    print("Migration 132: no-op (repairs a drifted 126, not itself revertible)")
