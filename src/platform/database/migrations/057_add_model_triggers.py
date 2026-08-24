"""
Migration to add triggers field to models table for storing LoRA trigger words
"""

from src.platform.database.database import db

def up():
    """Add triggers column to models table"""
    with db.get_cursor() as cursor:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'triggers' not in columns:
            # Add triggers column to models table (stores a JSON array string)
            cursor.execute('''
                ALTER TABLE models
                ADD COLUMN triggers TEXT
            ''')

def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    # SQLite doesn't support DROP COLUMN directly
    # Would need to recreate table without the column
    pass
