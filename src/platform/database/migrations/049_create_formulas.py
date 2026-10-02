from src.platform.database.database import db

_NAME = "049_create_formulas"

_CREATE_FORMULAS = (
    "CREATE TABLE IF NOT EXISTS formulas ("
    "id TEXT PRIMARY KEY, "
    "owner_id TEXT, "
    "preset_id TEXT NOT NULL, "
    "mode TEXT NOT NULL, "
    "variant TEXT, "
    "name TEXT NOT NULL, "
    "note TEXT, "
    "groups TEXT NOT NULL DEFAULT '[]', "
    "values_json TEXT NOT NULL DEFAULT '{}', "
    "signatures TEXT NOT NULL DEFAULT '{}', "
    "preset_version TEXT NOT NULL DEFAULT '', "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE)"
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(_CREATE_FORMULAS)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_formulas_owner_name "
            "ON formulas (owner_id, preset_id, mode, name COLLATE NOCASE)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_formulas_owner_scope "
            "ON formulas (owner_id, preset_id, mode)"
        )
    print(f"Migration {_NAME}: formulas created")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_formulas_owner_scope")
        cursor.execute("DROP INDEX IF EXISTS idx_formulas_owner_name")
        cursor.execute("DROP TABLE IF EXISTS formulas")
    print(f"Migration {_NAME}: formulas dropped")
