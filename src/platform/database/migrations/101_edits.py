from src.platform.database.database import db

_NAME = "101_edits"

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS edits ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "tool TEXT NOT NULL, "
    "name TEXT, "
    "source_kind TEXT NOT NULL, "
    "source_id TEXT NOT NULL, "
    "state TEXT NOT NULL, "
    "thumbnail_upload_id TEXT REFERENCES uploads(id) ON DELETE SET NULL, "
    "revision INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_edits_user_recent ON edits (user_id, updated_at DESC, id DESC)",
    "CREATE INDEX IF NOT EXISTS idx_edits_thumbnail ON edits (thumbnail_upload_id)",
)


def up():
    with db.get_cursor() as cursor:
        for statement in _STATEMENTS:
            cursor.execute(statement)
    print(f"Migration {_NAME}: edits table ready")
