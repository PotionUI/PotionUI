"""
Migration 045: Change autocomplete preview from path to file_id
Replaces preview_image_path with preview_file_id as a foreign key reference
to the files table for better consistency with the file storage system.
"""

from src.platform.database.database import db


def up():
    """Replace preview_image_path with preview_file_id in autocomplete_values"""
    with db.get_cursor() as cursor:
        # Add preview_file_id column
        cursor.execute("""
            ALTER TABLE autocomplete_values
            ADD COLUMN preview_file_id TEXT DEFAULT NULL
            REFERENCES files(id) ON DELETE SET NULL
        """)

        # We don't migrate old preview_image_path data since those images
        # weren't created through the file system and don't have file records.
        # Old preview images will need to be regenerated.

        # Drop preview_image_path column
        # SQLite doesn't support DROP COLUMN directly in older versions,
        # so we need to recreate the table
        cursor.execute("""
            CREATE TABLE autocomplete_values_new (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL REFERENCES autocomplete_categories(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1 NOT NULL,
                preview_file_id TEXT DEFAULT NULL REFERENCES files(id) ON DELETE SET NULL,
                preview_generation_id TEXT DEFAULT NULL,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT INTO autocomplete_values_new
            (id, category_id, label, value, sort_order, is_active, preview_file_id, preview_generation_id, user_id, created_at, updated_at)
            SELECT id, category_id, label, value, sort_order, is_active, preview_file_id, preview_generation_id, user_id, created_at, updated_at
            FROM autocomplete_values
        """)

        cursor.execute("DROP TABLE autocomplete_values")
        cursor.execute("ALTER TABLE autocomplete_values_new RENAME TO autocomplete_values")

        # Recreate indexes
        cursor.execute("CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_user_id ON autocomplete_values(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_sort_order ON autocomplete_values(sort_order)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_is_active ON autocomplete_values(is_active)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_preview_file_id ON autocomplete_values(preview_file_id)")

        # Recreate trigger
        cursor.execute("""
            CREATE TRIGGER update_autocomplete_values_updated_at
            AFTER UPDATE ON autocomplete_values
            FOR EACH ROW
            BEGIN
                UPDATE autocomplete_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        print("Migration 045: Changed autocomplete preview from path to file_id")


def down():
    """Revert to preview_image_path in autocomplete_values"""
    with db.get_cursor() as cursor:
        # Recreate table with preview_image_path instead of preview_file_id
        cursor.execute("""
            CREATE TABLE autocomplete_values_new (
                id TEXT PRIMARY KEY,
                category_id TEXT NOT NULL REFERENCES autocomplete_categories(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                value TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT 1 NOT NULL,
                preview_image_path TEXT DEFAULT NULL,
                preview_generation_id TEXT DEFAULT NULL,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT INTO autocomplete_values_new
            (id, category_id, label, value, sort_order, is_active, preview_generation_id, user_id, created_at, updated_at)
            SELECT id, category_id, label, value, sort_order, is_active, preview_generation_id, user_id, created_at, updated_at
            FROM autocomplete_values
        """)

        cursor.execute("DROP TABLE autocomplete_values")
        cursor.execute("ALTER TABLE autocomplete_values_new RENAME TO autocomplete_values")

        # Recreate indexes
        cursor.execute("CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_user_id ON autocomplete_values(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_sort_order ON autocomplete_values(sort_order)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_is_active ON autocomplete_values(is_active)")

        # Recreate trigger
        cursor.execute("""
            CREATE TRIGGER update_autocomplete_values_updated_at
            AFTER UPDATE ON autocomplete_values
            FOR EACH ROW
            BEGIN
                UPDATE autocomplete_values SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        print("Migration 045: Reverted autocomplete preview to image_path")
