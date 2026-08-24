"""
Add a self-referential `parent_id` to collections so they can be nested into a
folder tree. Deleting a folder cascade-deletes its subfolders (and their
memberships via the existing collection_generations FK).
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(collections)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'parent_id' not in columns:
            # NULL default keeps this a valid SQLite ADD COLUMN with a REFERENCES
            # clause; ON DELETE CASCADE gives us subtree deletion.
            cursor.execute('''
                ALTER TABLE collections
                ADD COLUMN parent_id TEXT REFERENCES collections(id) ON DELETE CASCADE
            ''')
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_collections_parent_id ON collections (parent_id)"
            )


def down():
    # SQLite doesn't support DROP COLUMN in the version used here; leave the
    # column in place (consistent with the other migrations' down()).
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_collections_parent_id")
