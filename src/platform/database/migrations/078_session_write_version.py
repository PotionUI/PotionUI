from src.platform.database.database import db

_NAME = "078_session_write_version"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "version" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        if "last_origin" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN last_origin TEXT")
    print(f"Migration {_NAME}: added sessions.version and sessions.last_origin")
