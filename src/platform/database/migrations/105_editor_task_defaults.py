from src.platform.database.database import db

_NAME = "105_editor_task_defaults"

_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS editor_task_defaults ("
    "user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
    "task TEXT NOT NULL, "
    "preset_id TEXT NOT NULL, "
    "mode TEXT NOT NULL, "
    "variant TEXT, "
    "setup_kind TEXT NOT NULL, "
    "setup_id TEXT, "
    "setup_name TEXT, "
    "preset_name TEXT, "
    "mode_label TEXT, "
    "set_at TEXT NOT NULL, "
    "PRIMARY KEY (user_id, task))",
)

def up():
    with db.get_cursor() as cursor:
        for statement in _STATEMENTS:
            cursor.execute(statement)

    print(f"Migration {_NAME}: applied")
