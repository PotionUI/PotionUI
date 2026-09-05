"""014 adds `idx_generations_user_created_id` to `generations`.

The history list is `WHERE user_id = ? ORDER BY created_at DESC, id DESC`, and
`001_baseline.py` only indexes `user_id` on its own - the ordering cost SQLite a
temp B-tree over every one of the user's rows before it could return the first
page. `001_baseline.py` is a frozen snapshot pinned by
`tests/platform/database/test_migration_001_baseline.py`, so the index is added
here rather than there; it runs after the baseline in filename order on a fresh
install and an upgrade alike.

The `id` column is part of the key because `created_at` has one-second
resolution: without it the tie-break sort still needs a temp B-tree whenever a
page boundary lands inside a burst of generations.

IDEMPOTENT: `CREATE INDEX IF NOT EXISTS`.
"""

from src.platform.database.database import db

_INDEX = "idx_generations_user_created_id"


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {_INDEX} "
            "ON generations (user_id, created_at DESC, id DESC)"
        )
    print(f"Migration 014_generations_user_created_index: created '{_INDEX}'")


def down():
    with db.get_cursor() as cursor:
        cursor.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    print(f"Migration 014_generations_user_created_index: dropped '{_INDEX}'")
