"""
Add `presets.configuration`: admin-set values for a preset's declared
`configuration:` schema (preset.yml, e.g. `checkpoint_tags: {type: model_tags}`).

Stored per installed preset (not per-user - configuration is an admin-only
concept, mirroring how the preset itself is installed once for everyone).
JSON-encoded dict, key -> value (e.g. `{"checkpoint_tags": ["tag_id_1", "tag_id_2"]}`).
Defaults to '{}' so every existing installed preset reads as "nothing configured"
without a backfill.

See docs/presets.md "Configuration (admin-set)".
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='presets'")
        if not cursor.fetchone():
            print("Migration 081: presets table doesn't exist yet, skipping")
            return

        if _has_column(cursor, "presets", "configuration"):
            print("Migration 081: presets.configuration already present, skipping")
            return

        cursor.execute("ALTER TABLE presets ADD COLUMN configuration TEXT NOT NULL DEFAULT '{}'")
        print("Migration 081: added presets.configuration")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='presets'")
        if not cursor.fetchone():
            return
        if not _has_column(cursor, "presets", "configuration"):
            return
        cursor.execute("ALTER TABLE presets DROP COLUMN configuration")
        print("Migration 081: dropped presets.configuration")
