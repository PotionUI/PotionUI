"""
Migration 133: Add model_metadata to models.

Per-model-type extensible metadata (e.g. a LoRA's default `strength`), keyed by
the field names declared in `ModelMetadataFieldRegistry`
(`src/platform/plugins/model_metadata_fields.py`). Stored as a JSON object
mapping field name -> value, edited through `PUT /api/models/{id}/metadata`.
"""

from src.platform.database.database import db


def up():
    """Add the model_metadata column to models."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'model_metadata' not in columns:
            cursor.execute('''
                ALTER TABLE models
                ADD COLUMN model_metadata TEXT
            ''')


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    # SQLite doesn't support DROP COLUMN directly
    # Would need to recreate table without the column
    pass
