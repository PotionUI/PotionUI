"""
Add `presets.form_overrides`: admin-set per-field form overrides.

`preset.yml`'s form fields declare their own `default`/`visible`/etc., but an
admin sometimes needs to pin, lock, or hide a specific field for every user of
an installed preset (e.g. force a checkpoint, hide an advanced knob) without
editing the preset's YAML. Stored per installed preset (mirroring 081's
`configuration` column), keyed by mode name and then field name:

    {"txt2img": {"steps": {"default": 30, "editable": false}}}

Applied to all form variants of the mode (see docs/presets.md "Form overrides
(admin-set)"). JSON-encoded dict; defaults to '{}' so every existing installed
preset reads as "nothing overridden" without a backfill.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='presets'")
        if not cursor.fetchone():
            print("Migration 088: presets table doesn't exist yet, skipping")
            return

        if _has_column(cursor, "presets", "form_overrides"):
            print("Migration 088: presets.form_overrides already present, skipping")
            return

        cursor.execute("ALTER TABLE presets ADD COLUMN form_overrides TEXT NOT NULL DEFAULT '{}'")
        print("Migration 088: added presets.form_overrides")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='presets'")
        if not cursor.fetchone():
            return
        if not _has_column(cursor, "presets", "form_overrides"):
            return
        cursor.execute("ALTER TABLE presets DROP COLUMN form_overrides")
        print("Migration 088: dropped presets.form_overrides")
