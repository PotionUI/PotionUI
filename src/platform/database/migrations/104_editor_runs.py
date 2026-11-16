from src.platform.database.database import db

_NAME = "104_editor_runs"

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS editor_runs ("
    "id TEXT PRIMARY KEY, "
    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "edit_id TEXT, "
    "step_id TEXT, "
    "task TEXT NOT NULL, "
    "generation_id TEXT, "
    "status TEXT NOT NULL, "
    "rect TEXT, "
    "crop TEXT, "
    "candidates TEXT, "
    "error TEXT, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_editor_runs_generation ON editor_runs (generation_id)",
    "CREATE INDEX IF NOT EXISTS idx_editor_runs_user_recent ON editor_runs (user_id, updated_at DESC)",
)


def _add_generations_origin(cursor):
    cursor.execute("PRAGMA table_info(generations)")
    existing = {row[1] for row in cursor.fetchall()}
    if "origin" not in existing:
        cursor.execute("ALTER TABLE generations ADD COLUMN origin TEXT")


def up():
    with db.get_cursor() as cursor:
        for statement in _STATEMENTS:
            cursor.execute(statement)
        _add_generations_origin(cursor)

    print(f"Migration {_NAME}: applied")
