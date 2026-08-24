"""
Migration 101: Add is_directory to models.

HF-layout LLM checkpoints (`config.json` + sharded `*.safetensors`
under `models/llm/<name>/`) are indexed as ONE catalog entry per directory,
identified by directory name. Unlike every other row, their `sha256` column
holds a cheap fingerprint (sha256 of config.json + sorted shard names/sizes),
not a hash of file content - hashing shards that can run tens of gigabytes on
every index pass is unusable. `is_directory` lets a reader (provider hash
lookup, the presets linter, anything else that treats `sha256` as a real
content hash) tell the two apart instead of guessing from `model_type`.
"""

from src.platform.database.database import db


def up():
    """Add the is_directory column to models."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'is_directory' not in columns:
            cursor.execute('''
                ALTER TABLE models
                ADD COLUMN is_directory INTEGER NOT NULL DEFAULT 0
            ''')


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    pass
