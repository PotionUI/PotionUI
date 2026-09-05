"""015 adds `history_revisions`, a per-user counter for history change detection.

`GET /api/generations/history/version` hands clients a token they compare for
equality instead of refetching the whole page to find out whether anything
moved. An aggregate over `generations` cannot serve as that token on its own:
tags, collection membership and attached files live in junction tables that
never touch a `generations` row, and `updated_at` has one-second resolution, so
a rating or favourite flipped inside the same second is invisible.

This table is bumped explicitly by every mutation that changes what the history
list would render, which makes the version check a single primary-key lookup
rather than a scan of the user's rows.

IDEMPOTENT: `CREATE TABLE IF NOT EXISTS`.
"""

from src.platform.database.database import db

_TABLE = "history_revisions"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                user_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    print(f"Migration 015_history_revisions: created '{_TABLE}'")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {_TABLE}")
    print(f"Migration 015_history_revisions: dropped '{_TABLE}'")
