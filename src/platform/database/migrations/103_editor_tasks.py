from src.platform.database.database import db

_NAME = "103_editor_tasks"


def _add_sessions_kind(cursor):
    cursor.execute("PRAGMA table_info(sessions)")
    existing = {row[1] for row in cursor.fetchall()}
    if "kind" not in existing:
        cursor.execute("ALTER TABLE sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'generation'")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_user_preset_kind ON sessions (user_id, preset_id, kind)"
    )


def up():
    with db.get_cursor() as cursor:
        _add_sessions_kind(cursor)

    print(f"Migration {_NAME}: applied")
