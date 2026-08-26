"""003 drops the `quick_search` default keybinding (key `/`, "Open quick
search dialog").

It was seeded by `001_baseline.py` alongside the other built-in shortcuts,
but no quick-search dialog or handler was ever built on the frontend - the
bound key has always been a no-op. `001_baseline.py` no longer seeds this row
on a fresh install; this migration removes it for every database that already
ran the baseline before this change landed.

Deleting the `keybinding_defaults` row is enough: `user_keybindings.action_id`
has `FOREIGN KEY ... REFERENCES keybinding_defaults(id) ON DELETE CASCADE`,
and `Database.get_connection()` sets `PRAGMA foreign_keys = ON` on every
connection, so any per-user override of `quick_search` is removed by SQLite
itself in the same statement - no separate cleanup of `user_keybindings` is
needed.

IDEMPOTENT. A second run deletes zero rows.
"""

import logging

from src.platform.database.database import db

logger = logging.getLogger(__name__)

_ACTION_ID = "quick_search"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM keybinding_defaults WHERE id = ?", (_ACTION_ID,)
        )
        removed = cursor.rowcount

        print(
            f"Migration 003_remove_quick_search_keybinding: removed "
            f"{removed} keybinding_defaults row(s) (and any cascaded "
            f"user_keybindings overrides) for '{_ACTION_ID}'"
        )


def down():
    print(
        "Migration 003_remove_quick_search_keybinding: no-op (the frontend "
        "no longer has anything to bind '/' to, so restoring the row would "
        "just reintroduce the dead shortcut)"
    )
