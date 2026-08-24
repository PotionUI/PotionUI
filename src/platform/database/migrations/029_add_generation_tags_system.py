"""
Migration to add generation tagging system with unified tag types
"""

from src.platform.database.database import db

def up():
    """Add type column to tags and create generation_tags table"""
    with db.get_cursor() as cursor:
        # Check if tags table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='tags'
        """)
        if not cursor.fetchone():
            # Tags table doesn't exist yet, skip this migration
            return

        # Check which columns already exist
        cursor.execute("PRAGMA table_info(tags)")
        tags_columns = [col[1] for col in cursor.fetchall()]

        # 1. Add type column to tags table (default MODEL for existing tags)
        if 'type' not in tags_columns:
            cursor.execute("ALTER TABLE tags ADD COLUMN type TEXT NOT NULL DEFAULT 'MODEL'")

        # 2. Add user_id column to tags table (nullable for backward compatibility)
        if 'user_id' not in tags_columns:
            cursor.execute("ALTER TABLE tags ADD COLUMN user_id TEXT")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tags_user_id ON tags(user_id)")

        # 3. Update all existing tags to type='MODEL'
        cursor.execute("UPDATE tags SET type = 'MODEL' WHERE type IS NULL OR type = ''")

        # 4. Create unique index for (name, type, user_id)
        # SQLite doesn't support DROP CONSTRAINT, so we work with indexes
        # The unique constraint on name will be replaced by this compound unique index
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_name_type_user
            ON tags(name, type, COALESCE(user_id, ''))
        """)

        # 5. Create generation_tags junction table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_tags (
                generation_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (generation_id, tag_id),
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        """)

        # 6. Create indexes for efficient querying
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_tags_generation_id
            ON generation_tags(generation_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_tags_tag_id
            ON generation_tags(tag_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tags_type_user
            ON tags(type, user_id)
        """)

def down():
    """Rollback changes"""
    with db.get_cursor() as cursor:
        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_generation_tags_generation_id")
        cursor.execute("DROP INDEX IF EXISTS idx_generation_tags_tag_id")
        cursor.execute("DROP INDEX IF EXISTS idx_tags_type_user")
        cursor.execute("DROP INDEX IF EXISTS idx_tags_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_tags_name_type_user")

        # Drop generation_tags table
        cursor.execute("DROP TABLE IF EXISTS generation_tags")

        # Note: SQLite doesn't easily support dropping columns
        # Would need to recreate the entire tags table to remove columns