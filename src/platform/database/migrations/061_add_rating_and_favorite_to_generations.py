"""
Add `rating` and `is_favorite` columns to generations so users can rate
(1-5 stars, 0 = unrated) and favorite generations from the history gallery.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(generations)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'rating' not in columns:
            cursor.execute('''
                ALTER TABLE generations
                ADD COLUMN rating INTEGER NOT NULL DEFAULT 0
            ''')

        if 'is_favorite' not in columns:
            cursor.execute('''
                ALTER TABLE generations
                ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0
            ''')


def down():
    # SQLite doesn't support DROP COLUMN in the version used here; leave columns in
    # place (consistent with other migrations' down()).
    pass
