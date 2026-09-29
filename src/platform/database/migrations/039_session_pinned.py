from src.platform.database.database import db

_NAME = "039_session_pinned"


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "pinned" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "pinned_at" not in columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN pinned_at TIMESTAMP")
    print(f"Migration {_NAME}: added sessions.pinned and sessions.pinned_at")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in cursor.fetchall()}
        if "pinned_at" in columns:
            cursor.execute("ALTER TABLE sessions DROP COLUMN pinned_at")
        if "pinned" in columns:
            cursor.execute("ALTER TABLE sessions DROP COLUMN pinned")
    print(f"Migration {_NAME}: dropped sessions.pinned and sessions.pinned_at")
