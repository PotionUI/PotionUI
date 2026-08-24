"""
Migration 018: Add autocomplete tables for user-defined prompt suggestions
Creates tables for storing hierarchical autocomplete categories and their values
"""

def up():
    """Create autocomplete tables"""
    from src.platform.database.database import db
    
    with db.get_cursor() as cursor:
        # Create autocomplete_categories table for hierarchical category structure
        cursor.execute("""
            CREATE TABLE autocomplete_categories (
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
        
        # Create autocomplete_values table for storing labels and values
        cursor.execute("""
            CREATE TABLE autocomplete_values (
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
        
        # Create triggers to update updated_at
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
        
        # Create indices for performance
        cursor.execute("CREATE INDEX idx_autocomplete_categories_path ON autocomplete_categories(path)")
        cursor.execute("CREATE INDEX idx_autocomplete_categories_user_id ON autocomplete_categories(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_categories_parent_id ON autocomplete_categories(parent_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_category_id ON autocomplete_values(category_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_user_id ON autocomplete_values(user_id)")
        cursor.execute("CREATE INDEX idx_autocomplete_values_sort_order ON autocomplete_values(sort_order)")
        
        print("Migration 018: Created autocomplete tables for user-defined prompt suggestions")


def down():
    """Drop autocomplete tables"""
    from src.platform.database.database import db
    
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_autocomplete_categories_updated_at")
        cursor.execute("DROP TRIGGER IF EXISTS update_autocomplete_values_updated_at")
        
        # Drop indices
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_path")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_categories_parent_id")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_category_id")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_user_id")
        cursor.execute("DROP INDEX IF EXISTS idx_autocomplete_values_sort_order")
        
        # Drop tables (values first due to foreign key)
        cursor.execute("DROP TABLE IF EXISTS autocomplete_values")
        cursor.execute("DROP TABLE IF EXISTS autocomplete_categories")
        
        print("Migration 018: Dropped autocomplete tables")