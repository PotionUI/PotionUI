from src.platform.database.database import db

_NAME = "102_edits_source_name"

def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(edits)")
        existing = {row[1] for row in cursor.fetchall()}
        if "source_name" not in existing:
            cursor.execute("ALTER TABLE edits ADD COLUMN source_name TEXT")

    print(f"Migration {_NAME}: applied")
