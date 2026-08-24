"""
Add provider_options JSON column to LLM configurations for provider-specific settings.

This allows storing provider-specific options like:
- Ollama: keep_alive, num_gpu, num_thread, num_ctx, etc.
- OpenAI: top_p, frequency_penalty, presence_penalty, seed, etc.
"""

from src.platform.database.database import db


def up():
    """Add provider_options column to llm_configurations table"""
    with db.get_cursor() as cursor:
        # Check if llm_configurations table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='llm_configurations'
        """)
        if not cursor.fetchone():
            # llm_configurations table doesn't exist yet, skip this migration
            return

        # Check if column already exists
        cursor.execute("PRAGMA table_info(llm_configurations)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'provider_options' not in columns:
            # Add provider_options column (JSON stored as TEXT)
            cursor.execute("""
                ALTER TABLE llm_configurations
                ADD COLUMN provider_options TEXT DEFAULT NULL
            """)


def down():
    """Remove provider_options column from llm_configurations table"""
    # SQLite doesn't support DROP COLUMN directly in older versions
    # For simplicity, we'll leave the column but it could be removed via table recreation
    pass
