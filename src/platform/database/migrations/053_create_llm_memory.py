"""
Migration 053: Create llm_memory table
Stores LLM memory entries with scoping by user, key, scope, and optional model.
"""

from src.platform.database.database import db


def up():
    """Create llm_memory table"""
    with db.get_cursor() as cursor:
        # Check if table already exists (idempotent)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='llm_memory'")
        if cursor.fetchone():
            print("Migration 053: llm_memory table already exists, skipping")
            return

        cursor.execute("""
            CREATE TABLE llm_memory (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                model_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX idx_llm_memory_key_scope
            ON llm_memory(user_id, key, scope, COALESCE(model_id, ''))
        """)

        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_scope
            ON llm_memory(user_id, scope)
        """)

        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_model
            ON llm_memory(user_id, model_id)
        """)

        print("Migration 053: Created llm_memory table with indexes")


def down():
    """Drop llm_memory table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS llm_memory")
        print("Migration 053: Dropped llm_memory table")
