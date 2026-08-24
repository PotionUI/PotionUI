"""
Migration 044: Add active state and preview images to autocomplete tables
Adds is_active field to categories and values for soft-delete mechanism,
and preview_image_path/preview_generation_id to values for preview images.
"""

from src.platform.database.database import db


def up():
    """Add active state and preview image columns to autocomplete tables"""
    with db.get_cursor() as cursor:
        # Add is_active column to autocomplete_categories
        cursor.execute("""
            ALTER TABLE autocomplete_categories
            ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL
        """)

        # Add columns to autocomplete_values
        cursor.execute("""
            ALTER TABLE autocomplete_values
            ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL
        """)

        cursor.execute("""
            ALTER TABLE autocomplete_values
            ADD COLUMN preview_image_path TEXT DEFAULT NULL
        """)

        cursor.execute("""
            ALTER TABLE autocomplete_values
            ADD COLUMN preview_generation_id TEXT DEFAULT NULL
        """)

        # Create indexes for is_active columns
        cursor.execute("""
            CREATE INDEX idx_autocomplete_categories_is_active
            ON autocomplete_categories(is_active)
        """)

        cursor.execute("""
            CREATE INDEX idx_autocomplete_values_is_active
            ON autocomplete_values(is_active)
        """)

        print("Migration 044: Added active state and preview image columns to autocomplete tables")


def down():
    """Remove active state and preview image columns from autocomplete tables"""
    with db.get_cursor() as cursor:
        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_is_active")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_is_active")

        # SQLite doesn't support DROP COLUMN directly in older versions,
        # but modern SQLite (3.35+) does. For compatibility, we recreate tables.

        # Recreate autocomplete_categories without new column
        cursor.execute("""
            CREATE TABLE autocomplete_categories_new (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                parent_id TEXT REFERENCES autocomplete_categories(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                description TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(path, user_id)
            )
        """)

        cursor.execute("""
            INSERT INTO autocomplete_categories_new
            SELECT id, name, path, parent_id, user_id, description, created_at, updated_at
            FROM autocomplete_categories
        """)

        cursor.execute("DROP TABLE autocomplete_categories")
        cursor.execute("ALTER TABLE autocomplete_categories_new RENAME TO autocomplete_categories")

        # Recreate autocomplete_values without new columns
        cursor.execute("""
            CREATE TABLE autocomplete_values_new (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL REFERENCES autocomplete_categories(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT INTO autocomplete_values_new
            SELECT id, category_id, label, value, sort_order, user_id, created_at, updated_at
            FROM autocomplete_values
        """)

        cursor.execute("DROP TABLE autocomplete_values")
        cursor.execute("ALTER TABLE autocomplete_values_new RENAME TO autocomplete_values")

        # Recreate original indexes
        cursor.execute("CREATE INDEX idx_autocomplete_categories_path ON autocomplete_categories(path)")
        cursor.execute("CREATE INDEX idx_autocomplete_categories_user_id ON autocomplete_categories(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_categories_parent_id ON autocomplete_categories(parent_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_user_id ON autocomplete_values(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_sort_order ON autocomplete_values(sort_order)")

        # Recreate triggers
        cursor.execute("""
            CREATE TRIGGER update_autocomplete_categories_updated_at
            AFTER UPDATE ON autocomplete_categories
            FOR EACH ROW
            BEGIN
                UPDATE autocomplete_categories SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        cursor.execute("""
            CREATE TRIGGER update_autocomplete_values_updated_at
            AFTER UPDATE ON autocomplete_values
            FOR EACH ROW
            BEGIN
                UPDATE autocomplete_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        print("Migration 044: Removed active state and preview image columns from autocomplete tables")
