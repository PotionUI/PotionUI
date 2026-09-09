"""Migration 023: where backups go, how many are kept, and which tier is taken.

The `potionui backup` command already writes archives; these three SYSTEM
settings are what the admin panel edits and what the command reads for its
default `--out`, so a scheduled run and a run from the panel land in the same
directory.

IDEMPOTENT: each setting is inserted only when absent.
"""

from datetime import datetime

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid

_SETTINGS = (
    (
        "backup_destination",
        "backups",
        "string",
        "Directory backup archives are written to, relative to the install or absolute.",
    ),
    (
        "backup_retention",
        "7",
        "integer",
        "How many backup archives to keep in that directory. 0 keeps everything.",
    ),
    (
        "backup_default_tier",
        "config",
        "string",
        "What a backup takes by default: config, media (config plus a media mirror) or all.",
    ),
)


def up():
    seeded = []
    with db.get_cursor() as cursor:
        now = datetime.now().isoformat()
        for key, value, value_type, description in _SETTINGS:
            cursor.execute("SELECT 1 FROM settings WHERE key = ?", (key,))
            if cursor.fetchone() is not None:
                continue
            cursor.execute(
                """
                INSERT INTO settings (id, key, value, value_type, description, type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'SYSTEM', ?, ?)
                """,
                (generate_ulid(), key, value, value_type, description, now, now),
            )
            seeded.append(key)

    print(f"Migration 023_backup_settings: seeded {len(seeded)} setting(s)")


def down():
    with db.get_cursor() as cursor:
        for key, _, _, _ in _SETTINGS:
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    print("Migration 023_backup_settings: removed the backup settings")
