
from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(files)")
        existing = {row[1] for row in cursor.fetchall()}
        if "has_alpha" not in existing:
            cursor.execute("ALTER TABLE files ADD COLUMN has_alpha INTEGER NOT NULL DEFAULT 0")

    print("Migration 029_files_has_alpha: applied")


def down():
    print("Migration 029_files_has_alpha: no-op (SQLite keeps the added column)")
