"""
Migration 103: Add avatar_filename to users.

A user's uploaded avatar is stored under `storage/avatars/` as a
`{uuid4}{ext}` file; this column is the only pointer to it. NULL means no
avatar has been uploaded (the frontend falls back to a placeholder).
"""

from src.platform.database.database import db


def up():
    """Add the avatar_filename column to users."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'avatar_filename' not in columns:
            cursor.execute('''
                ALTER TABLE users
                ADD COLUMN avatar_filename TEXT
            ''')


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    pass
