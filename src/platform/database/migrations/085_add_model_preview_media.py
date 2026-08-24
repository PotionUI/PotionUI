"""
Migration 085: Add preview_media to models.

Per-model admin-set preview: an image, video, or audio clip uploaded through the
media feature (MediaLoader) and chosen in the admin model-details modal. Stored as
a JSON object ({url, type, name?, relative_path?}) referencing an uploaded media
item served via /api/media/uploads/. A locally-set preview takes precedence over
marketplace-supplied preview files wherever a model preview renders.
"""

from src.platform.database.database import db


def up():
    """Add the preview_media column to models."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'preview_media' not in columns:
            cursor.execute('''
                ALTER TABLE models
                ADD COLUMN preview_media TEXT
            ''')


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    # SQLite doesn't support DROP COLUMN directly
    # Would need to recreate table without the column
    pass
