"""
Migration 049: Create model_prompts table
Creates the model_prompts table for storing prompts associated with models from external providers.
"""

from src.platform.database.database import db


def up():
    """Create model_prompts table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_prompts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                negative_prompt TEXT,
                source_provider TEXT,
                source_id TEXT,
                model_id TEXT,
                model_name TEXT,
                base_model TEXT,
                cfg_scale REAL,
                steps INTEGER,
                sampler TEXT,
                width INTEGER,
                height INTEGER,
                heart_count INTEGER DEFAULT 0,
                like_count INTEGER DEFAULT 0,
                laugh_count INTEGER DEFAULT 0,
                cry_count INTEGER DEFAULT 0,
                comment_count INTEGER DEFAULT 0,
                tags TEXT,
                nsfw BOOLEAN DEFAULT FALSE,
                metadata TEXT,
                source_url TEXT,
                embedded BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_model_prompts_source
            ON model_prompts(user_id, source_provider, source_id)
            WHERE source_id IS NOT NULL
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_prompts_user
            ON model_prompts(user_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_prompts_base_model
            ON model_prompts(base_model)
        """)

        print("Migration 049: Created model_prompts table")


def down():
    """Drop model_prompts table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS model_prompts")

        print("Migration 049: Reverted model_prompts table")
