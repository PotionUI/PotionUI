"""
Migration 065: Create generation_segments and generation_segment_autocomplete tables.

Makes prompt "segments" (the resolved chip/timeline pieces that make up a generation's
prompt) first-class, queryable records, and tracks which saved prompt (model_prompts)
and which autocomplete value fed each segment. See
`src/persistence/models/generation_segment.py` /
`src/persistence/repositories/generation_segment_repository.py` for the model/repository
pair that reads and writes these tables.
"""

from src.platform.database.database import db


def up():
    """Create generation_segments and generation_segment_autocomplete tables and their indexes."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_segments (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                channel TEXT,
                prompt_index INTEGER DEFAULT 0,
                segment_index INTEGER,
                segment_type TEXT,
                text TEXT,
                title TEXT,
                is_disabled INTEGER DEFAULT 0,
                source_prompt_id TEXT,
                source_prompt_modified INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (source_prompt_id) REFERENCES model_prompts(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_segments_generation_id
            ON generation_segments(generation_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_segments_source_prompt_id
            ON generation_segments(source_prompt_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_segment_autocomplete (
                id TEXT PRIMARY KEY,
                segment_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                autocomplete_value_id TEXT,
                category_path TEXT,
                value TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (segment_id) REFERENCES generation_segments(id) ON DELETE CASCADE,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (autocomplete_value_id) REFERENCES autocomplete_values(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_segment_autocomplete_segment_id
            ON generation_segment_autocomplete(segment_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_segment_autocomplete_generation_id
            ON generation_segment_autocomplete(generation_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_segment_autocomplete_value_id
            ON generation_segment_autocomplete(autocomplete_value_id)
        """)


def down():
    """Drop generation_segments and generation_segment_autocomplete tables."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS generation_segment_autocomplete")
        cursor.execute("DROP TABLE IF EXISTS generation_segments")
