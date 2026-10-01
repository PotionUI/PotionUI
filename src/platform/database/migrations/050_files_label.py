from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(files)")
        existing = {row[1] for row in cursor.fetchall()}
        if "label" not in existing:
            cursor.execute("ALTER TABLE files ADD COLUMN label TEXT")

    print("Migration 050_files_label: applied")


def down():
    print("Migration 050_files_label: no-op (SQLite keeps the added column)")
