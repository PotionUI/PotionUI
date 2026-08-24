"""
Add `collections` and `collection_generations` tables.

Collections are named, user-owned virtual groupings of generations, independent
of tags. The `collection_generations` junction links generations into a
collection; `generation_repository` INNER JOINs it to filter history by
collection.
"""

from src.platform.database.database import db


def up():
    """Create collections + collection_generations tables"""
    with db.get_cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collections'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX idx_collections_user_id ON collections (user_id)")

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='collection_generations'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE collection_generations (
                    collection_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (collection_id, generation_id),
                    FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
                    FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
                )
            """)
            cursor.execute("CREATE INDEX idx_collection_generations_collection_id ON collection_generations (collection_id)")
            cursor.execute("CREATE INDEX idx_collection_generations_generation_id ON collection_generations (generation_id)")


def down():
    """Drop collections + collection_generations tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_collection_generations_collection_id")
        cursor.execute("DROP INDEX IF EXISTS idx_collection_generations_generation_id")
        cursor.execute("DROP TABLE IF EXISTS collection_generations")
        cursor.execute("DROP INDEX IF EXISTS idx_collections_user_id")
        cursor.execute("DROP TABLE IF EXISTS collections")
