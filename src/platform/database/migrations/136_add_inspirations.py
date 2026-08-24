"""Migration 136: Inspirations - cross-user publishing of generations.

An inspiration is a snapshot, not a reference: the published files are copied
into `storage/inspirations/<inspiration_id>/` and the generating params are
embedded in `inspirations.params_snapshot`. `source_generation_id` is kept for
provenance only - an inspiration must survive deletion of the source
generation, so nothing here has a foreign key back to `generations`.
"""

from src.platform.database.database import db


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def up():
    with db.get_cursor() as cursor:
        if not _table_exists(cursor, "inspirations"):
            cursor.execute("""
                CREATE TABLE inspirations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    media TEXT NOT NULL DEFAULT '[]',
                    params_snapshot TEXT NOT NULL DEFAULT '{}',
                    preset_id TEXT,
                    preset_name TEXT,
                    source_generation_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX idx_inspirations_user_id ON inspirations (user_id)")
            cursor.execute("CREATE INDEX idx_inspirations_created_at ON inspirations (created_at)")

        if not _table_exists(cursor, "inspiration_comments"):
            cursor.execute("""
                CREATE TABLE inspiration_comments (
                    id TEXT PRIMARY KEY,
                    inspiration_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_inspiration_comments_inspiration_id "
                "ON inspiration_comments (inspiration_id)"
            )

        if not _table_exists(cursor, "inspiration_collections"):
            cursor.execute("""
                CREATE TABLE inspiration_collections (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    parent_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES inspiration_collections(id) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_inspiration_collections_user_id ON inspiration_collections (user_id)"
            )
            cursor.execute(
                "CREATE INDEX idx_inspiration_collections_parent_id ON inspiration_collections (parent_id)"
            )

        if not _table_exists(cursor, "inspiration_collection_items"):
            cursor.execute("""
                CREATE TABLE inspiration_collection_items (
                    collection_id TEXT NOT NULL,
                    inspiration_id TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(collection_id, inspiration_id),
                    FOREIGN KEY (collection_id) REFERENCES inspiration_collections(id) ON DELETE CASCADE,
                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_inspiration_collection_items_collection_id "
                "ON inspiration_collection_items (collection_id)"
            )
            cursor.execute(
                "CREATE INDEX idx_inspiration_collection_items_inspiration_id "
                "ON inspiration_collection_items (inspiration_id)"
            )

        if not _table_exists(cursor, "inspiration_saves"):
            cursor.execute("""
                CREATE TABLE inspiration_saves (
                    user_id TEXT NOT NULL,
                    inspiration_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, inspiration_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (inspiration_id) REFERENCES inspirations(id) ON DELETE CASCADE
                )
            """)
            cursor.execute(
                "CREATE INDEX idx_inspiration_saves_inspiration_id ON inspiration_saves (inspiration_id)"
            )
            cursor.execute(
                "CREATE INDEX idx_inspiration_saves_user_id ON inspiration_saves (user_id)"
            )

    print("Migration 136: added inspirations, inspiration_comments, inspiration_collections, "
          "inspiration_collection_items, inspiration_saves")


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS inspiration_saves")
        cursor.execute("DROP TABLE IF EXISTS inspiration_collection_items")
        cursor.execute("DROP TABLE IF EXISTS inspiration_collections")
        cursor.execute("DROP TABLE IF EXISTS inspiration_comments")
        cursor.execute("DROP TABLE IF EXISTS inspirations")

    print("Migration 136: dropped inspirations tables")
