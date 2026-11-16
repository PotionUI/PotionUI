from src.platform.database.database import db

_NAME = "099_uploads_has_audio"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(uploads)")
        existing = {row[1] for row in cursor.fetchall()}
        if "has_audio" not in existing:
            cursor.execute("ALTER TABLE uploads ADD COLUMN has_audio INTEGER")

    print(f"Migration {_NAME}: applied")
