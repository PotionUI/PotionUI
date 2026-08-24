"""
Migration 120: purpose discriminator on `uploads`.

Every upload lands as a first-class Library resource today, but not every
upload IS one: `MediaEditors.svelte` saves a painted inpainting mask through
`POST /api/media/upload` because the mask has to be addressable by path (the
`${name}_inpaint_mask` sibling channel references it that way), not because
the user wants to browse it later. The result is `mask-<timestamp>.png` rows
cluttering the Library alongside real uploads.

`purpose` names why a row exists. `'user_upload'` is a real Library resource
(a direct upload or a copied generation file); `'derived_artifact'` is a file
that had to exist on disk for some other feature to reference by path and was
never meant to be browsed. Every existing row predates the distinction and
was, by construction, someone deliberately uploading a file to use later - so
the backfill is unconditionally `'user_upload'`, matching the column default.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "uploads", "purpose"):
            print("Migration 120: uploads.purpose already present, skipping")
            return

        cursor.execute(
            "ALTER TABLE uploads ADD COLUMN purpose TEXT NOT NULL DEFAULT 'user_upload'"
        )
        print("Migration 120: added uploads.purpose (backfilled 'user_upload')")


def down():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "uploads", "purpose"):
            print("Migration 120: uploads.purpose absent, nothing to drop")
            return
        cursor.execute("ALTER TABLE uploads DROP COLUMN purpose")
        print("Migration 120: dropped uploads.purpose")
