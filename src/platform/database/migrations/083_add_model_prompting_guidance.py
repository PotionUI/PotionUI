"""
Migration 083: Add prompting_guidance to models.

Per-model admin-authored text that teaches the chat LLM how to write prompts
for that diffusion model (replaces the deleted "LLM styles" feature). Injected
as a labelled system block by ChatContextBuilder.inject_model_guidance_block
when the chat session's active model is this model.
"""

from src.platform.database.database import db


def up():
    """Add the prompting_guidance column to models."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(models)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'prompting_guidance' not in columns:
            cursor.execute('''
                ALTER TABLE models
                ADD COLUMN prompting_guidance TEXT
            ''')


def down():
    """Rollback the migration - SQLite doesn't support dropping columns easily"""
    # SQLite doesn't support DROP COLUMN directly
    # Would need to recreate table without the column
    pass
