"""Migration 016: seeds `workbench_single_result_gallery`, a SYSTEM setting
admins use to decide whether the workbench gallery strip renders when a
generation produced exactly one output. Default `false` - a single result
fills the workbench, no strip below it.

IDEMPOTENT: the setting is only inserted when absent.
"""

from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_SETTING_KEY = "workbench_single_result_gallery"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT 1 FROM settings WHERE key = ?", (_SETTING_KEY,))
        seeded = cursor.fetchone() is None
        if seeded:
            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_ulid(), _SETTING_KEY, "false", "boolean",
                    "Show the result gallery strip when a run produced a single output",
                    "SYSTEM", now, now,
                ),
            )
    print(
        f"Migration 016_workbench_single_result_gallery: setting {'seeded' if seeded else 'already present'}"
    )


def down():
    print("Migration 016_workbench_single_result_gallery: no-op (SQLite keeps the seeded row)")
