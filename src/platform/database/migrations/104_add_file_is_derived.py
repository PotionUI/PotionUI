"""
Migration 104: Add is_derived column to files table.

A derived file is produced from another final file of the same generation
(e.g. Krea-2's inline enhance pass, whose gallery continues the file index
sequence - see d0d0a7e7). The flag is presentation-layer metadata: persisted
file order and indices stay untouched so index-based mappings (Civitai export
fallback, per-index parameter lookups) keep working.
"""

from src.platform.database.database import db


def up():
    """Add is_derived column to files table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='files'
        """)
        if not cursor.fetchone():
            return

        cursor.execute("PRAGMA table_info(files)")
        files_columns = [col[1] for col in cursor.fetchall()]

        if 'is_derived' not in files_columns:
            cursor.execute("""
                ALTER TABLE files ADD COLUMN is_derived INTEGER NOT NULL DEFAULT 0
            """)


def down():
    """Remove is_derived column from files table"""
    # SQLite doesn't support DROP COLUMN directly; leave the column in place,
    # same tradeoff as 086_add_file_video_metadata.
    pass
