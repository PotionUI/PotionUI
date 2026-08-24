"""
Migration to add description field to models table for markdown content
"""

from src.platform.database.database import db

def up():
    """Add description column to models table"""
    with db.get_cursor() as cursor:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'description' not in columns:
            # Add description column to models table
            cursor.execute('''
                ALTER TABLE models 
                ADD COLUMN description TEXT
            ''')

def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    # SQLite doesn't support DROP COLUMN directly
    # Would need to recreate table without the column
    pass