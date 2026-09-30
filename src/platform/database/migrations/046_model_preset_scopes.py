from src.platform.database.database import db

_NAME = "046_model_preset_scopes"

_CREATE_SCOPES = (
    "CREATE TABLE IF NOT EXISTS model_preset_scopes ("
    "model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE, "
    "preset_id TEXT NOT NULL, "
    "created_at TEXT NOT NULL, "
    "PRIMARY KEY (model_id, preset_id))"
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(_CREATE_SCOPES)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_model_preset_scopes_preset ON model_preset_scopes (preset_id)")
    print(f"Migration {_NAME}: model preset scopes created")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_model_preset_scopes_preset")
        cursor.execute("DROP TABLE IF EXISTS model_preset_scopes")
    print(f"Migration {_NAME}: model preset scopes dropped")
