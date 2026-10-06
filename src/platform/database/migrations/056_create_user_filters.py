from src.platform.database.database import db

_NAME = "056_create_user_filters"

_CREATE_USER_FILTERS = (
    "CREATE TABLE IF NOT EXISTS user_filters ("
    "id TEXT PRIMARY KEY, "
    "owner_id TEXT NOT NULL, "
    "name TEXT NOT NULL, "
    "description TEXT, "
    "group_name TEXT NOT NULL DEFAULT 'Mine', "
    "intensity INTEGER NOT NULL DEFAULT 100, "
    "steps_json TEXT NOT NULL DEFAULT '[]', "
    "schema_version INTEGER NOT NULL DEFAULT 1, "
    "created_at TEXT NOT NULL, "
    "updated_at TEXT NOT NULL, "
    "FOREIGN KEY (owner_id) REFERENCES users (id) ON DELETE CASCADE)"
)


def up():
    with db.get_cursor() as cursor:
        cursor.execute(_CREATE_USER_FILTERS)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_filters_owner_name "
            "ON user_filters (owner_id, name COLLATE NOCASE)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_filters_owner "
            "ON user_filters (owner_id, updated_at)"
        )
    print(f"Migration {_NAME}: user_filters created")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_user_filters_owner")
        cursor.execute("DROP INDEX IF EXISTS idx_user_filters_owner_name")
        cursor.execute("DROP TABLE IF EXISTS user_filters")
    print(f"Migration {_NAME}: user_filters dropped")
