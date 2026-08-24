"""
Add thumbnail columns to files table for storing generated thumbnail paths
"""

from src.platform.database.database import db

def up():
    """Add thumbnail columns to files table"""
    with db.get_cursor() as cursor:
        # Check if files table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            # Files table doesn't exist yet, skip this migration
            return

        # Check which columns already exist
        cursor.execute("PRAGMA table_info(files)")
        files_columns = [col[1] for col in cursor.fetchall()]

        # Add thumbnail columns to files table if they don't exist
        if 'thumbnail_small' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN thumbnail_small TEXT
            """)

        if 'thumbnail_medium' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN thumbnail_medium TEXT
            """)

        if 'thumbnail_large' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN thumbnail_large TEXT
            """)

def down():
    """Remove thumbnail columns from files table"""
    with db.get_cursor() as cursor:
        # SQLite doesn't support DROP COLUMN directly
        # We would need to recreate the table, but for now we'll leave the columns
        # as they can be safely ignored if not used
        pass