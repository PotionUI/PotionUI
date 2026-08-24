"""Migration 140: `inspirations.technique`.

An open string classifying an inspiration's generation shape ('txt2img',
'img2img', 'txt2vid', 'img2vid', 'vid2vid', 'upscale', ...), derived once at
publish time (src.features.inspirations.technique.derive_technique) from the
source generation's mode/preset - never recomputed on read. Existing rows
backfill to NULL (unknown), not a guessed default: nothing here has enough
information left to derive it after the fact.
"""

from src.platform.database.database import db


def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def up():
    with db.get_cursor() as cursor:
        if not _has_column(cursor, "inspirations", "technique"):
            cursor.execute("ALTER TABLE inspirations ADD COLUMN technique TEXT")
        print("Migration 140: added inspirations.technique")


def down():
    with db.get_cursor() as cursor:
        if _has_column(cursor, "inspirations", "technique"):
            cursor.execute("ALTER TABLE inspirations DROP COLUMN technique")
        print("Migration 140: dropped inspirations.technique")
