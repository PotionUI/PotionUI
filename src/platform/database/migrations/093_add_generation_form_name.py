"""
Migration 093: give `generations` its resolved form variant.

`bind_form` (src/features/forms/binding.py) already resolves which form
variant a submission bound against - `docs/presets.md` "Variants" - into
`BoundForm.form_name` (falls back to the mode's default variant when the
client submitted none), but the orchestrator only ever used that value to
call `bind_form`, never to record it. History has no way to tell which
variant produced a given generation, so "Reuse" (History -> Generate) could
restore `preset_id`/`mode`/`form_data`/`prompt_state` but not the variant
that combination was actually validated and rendered against - reuse could
silently re-resolve to the mode's CURRENT default variant instead.

`form_name` is plain, denormalized TEXT, exactly like `generation_stats`'s
`preset_name` (migration 091) and this table's own `mode` column: resolved
once at write time, never re-derived, and NULL simply reads as "unknown
variant" for rows written before this migration (never backfilled - the
mode's default is not necessarily what an old row actually used, so guessing
would be dishonest).

See src/features/generation/orchestrator.py (`bound.form_name` passed to the
`Generation(...)` constructor) and src/features/generation/records.py
(`Generation.form_name`, `to_dict()`).
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "generations", "form_name"):
            print("Migration 093: generations.form_name already present, skipping")
            return

        cursor.execute("ALTER TABLE generations ADD COLUMN form_name TEXT")
        print("Migration 093: added generations.form_name")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "generations", "form_name"):
            print("Migration 093: generations.form_name absent, nothing to drop")
            return

        cursor.execute("ALTER TABLE generations DROP COLUMN form_name")
        print("Migration 093: dropped generations.form_name")
