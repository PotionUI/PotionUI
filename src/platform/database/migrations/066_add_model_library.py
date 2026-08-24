"""
Add user-level model library tables: `user_model_meta` (per-user favorite/custom
name for a model), `model_collections` (named, user-owned virtual groupings of
models, mirroring the generation `collections` table), and
`model_collection_members` (the collection <-> model junction table).
"""

from src.platform.database.database import db


def up():
    """Create user_model_meta, model_collections, and model_collection_members tables"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_model_meta (
                user_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                custom_name TEXT,
                is_favorite INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, model_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_model_meta_fav ON user_model_meta (user_id, is_favorite)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                parent_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (parent_id) REFERENCES model_collections(id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_collections_user_id ON model_collections (user_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_collections_parent_id ON model_collections (parent_id)"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_collection_members (
                collection_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collection_id, model_id),
                FOREIGN KEY (collection_id) REFERENCES model_collections(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_collection_members_collection_id ON model_collection_members (collection_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_model_collection_members_model_id ON model_collection_members (model_id)"
        )


def down():
    """Drop user_model_meta, model_collections, and model_collection_members tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_model_collection_members_collection_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_collection_members_model_id")
        cursor.execute("DROP TABLE IF EXISTS model_collection_members")
        cursor.execute("DROP INDEX IF EXISTS idx_model_collections_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_collections_parent_id")
        cursor.execute("DROP TABLE IF EXISTS model_collections")
        cursor.execute("DROP INDEX IF EXISTS idx_user_model_meta_fav")
        cursor.execute("DROP TABLE IF EXISTS user_model_meta")
