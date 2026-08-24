"""
Migration 086: Add duration_seconds and fps columns to files table.

Companion to 026_add_file_dimensions (width/height): videos gained persisted
dimensions there, but duration and frame rate were never stored, so any UI
that wants to show them (e.g. MediaSelect metadata) has nothing
to read for files that predate this migration -- those simply show no
duration/fps, same as they show no width/height for the same reason.
"""

from src.platform.database.database import db


def up():
    """Add duration_seconds and fps columns to files table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            # Files table doesn't exist yet, skip this migration
            return

        cursor.execute("PRAGMA table_info(files)")
        files_columns = [col[1] for col in cursor.fetchall()]

        if 'duration_seconds' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN duration_seconds REAL
            """)

        if 'fps' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN fps REAL
            """)


def down():
    """Remove duration_seconds and fps columns from files table"""
    # SQLite doesn't support DROP COLUMN directly; leave the columns in place,
    # same tradeoff 026_add_file_dimensions made for width/height.
    pass
