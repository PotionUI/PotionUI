"""
Add width and height columns to files table for storing actual file dimensions
"""

from src.platform.database.database import db

def up():
    """Add width and height columns to files table"""
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

        # Add width column to files table if it doesn't exist
        if 'width' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN width INTEGER
            """)

        # Add height column to files table if it doesn't exist
        if 'height' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN height INTEGER
            """)

def down():
    """Remove width and height columns from files table"""
    with db.get_cursor() as cursor:
        # SQLite doesn't support DROP COLUMN directly
        # We would need to recreate the table, but for now we'll leave the columns
        # as they can be safely ignored if not used
        pass