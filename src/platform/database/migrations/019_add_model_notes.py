"""
Add user_notes field to models table for storing user-defined tips and notes
"""

from src.platform.database.database import db

def up():
    """Add user_notes field to models table"""
    with db.get_cursor() as cursor:
        # Check if models table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='models'
        """)
        if not cursor.fetchone():
            # Models table doesn't exist yet, skip this migration
            return

        # Check if column already exists
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'user_notes' not in columns:
            cursor.execute("""
                ALTER TABLE models
                ADD COLUMN user_notes TEXT DEFAULT NULL
            """)

def down():
    """Remove user_notes field from models table (SQLite doesn't support DROP COLUMN directly)"""
    # SQLite doesn't support DROP COLUMN, so we'd need to recreate the table
    # For now, leaving as is since this is not critical
    pass