"""
Migration to add tags system for models
"""

from src.platform.database.database import db

def up():
    """Create tags system tables"""
    with db.get_cursor() as cursor:
        # Create tags table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create model_tags junction table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_tags (
                model_id TEXT NOT NULL,
                tag_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (model_id, tag_id),
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
        ''')
        
        # Create indexes for efficient querying
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_tags_model_id 
            ON model_tags(model_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_model_tags_tag_id 
            ON model_tags(tag_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_tags_name 
            ON tags(name)
        ''')

def down():
    """Drop tags system tables"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_model_tags_model_id")
        cursor.execute("DROP INDEX IF EXISTS idx_model_tags_tag_id")
        cursor.execute("DROP INDEX IF EXISTS idx_tags_name")
        cursor.execute("DROP TABLE IF EXISTS model_tags")
        cursor.execute("DROP TABLE IF EXISTS tags")