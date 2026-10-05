from src.platform.database.database import db

_INDEXES = (
    ("idx_uploads_user_favorite", "uploads", "(user_id, is_favorite)"),
    ("idx_prompts_user_favorite", "prompts", "(user_id, is_favorite)"),
    ("idx_collections_parent_user", "collections", "(parent_id, user_id)"),
    ("idx_model_collections_parent_user", "model_collections", "(parent_id, user_id)"),
    ("idx_inspiration_collections_parent_user", "inspiration_collections", "(parent_id, user_id)"),
)


def _columns(cursor, table):
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def up():
    with db.get_cursor() as cursor:
        for table in ("uploads", "prompts"):
            if "is_favorite" not in _columns(cursor, table):
                cursor.execute(
                    f"ALTER TABLE {table} ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0"
                )
        for name, table, columns in _INDEXES:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} {columns}")

    print("Migration 051_collection_favorites_and_indexes: applied")


def down():
    with db.get_cursor() as cursor:
        for name, _table, _columns_sql in _INDEXES:
            cursor.execute(f"DROP INDEX IF EXISTS {name}")

    print("Migration 051_collection_favorites_and_indexes: dropped indexes, kept columns")
