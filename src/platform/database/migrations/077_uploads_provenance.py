from src.platform.database.database import db

_NAME = "077_uploads_provenance"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(uploads)")
        existing = {row[1] for row in cursor.fetchall()}
        if "provenance" not in existing:
            cursor.execute("ALTER TABLE uploads ADD COLUMN provenance TEXT")

    print(f"Migration {_NAME}: applied")
