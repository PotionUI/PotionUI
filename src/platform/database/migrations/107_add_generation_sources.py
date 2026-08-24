"""
Migration 107: Create generation_sources table (provenance).

Records that a generation's media field (`<field>`) was seeded from a prior
generation's output (`source_generation_id`, `source_file_index`) rather than
a bare uploaded/picked file, via a `<field>__origin` sibling key in form_data
(see `src/features/forms/binding.py`'s passthrough rule and
`src/features/generation/orchestrator.py`'s submission-time validation). The
read side (`GenerationHistoryQuery.get_params`) follows this link to inherit
prompt/seed/cfg/models from the source generation when the enhance run's own
values are missing or empty.
"""

from src.platform.database.database import db


def up():
    """Create generation_sources and its indexes."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS generation_sources (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                field_name TEXT NOT NULL,
                source_generation_id TEXT NOT NULL,
                source_file_index INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (source_generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                UNIQUE(generation_id, field_name)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_sources_generation_id
            ON generation_sources(generation_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_generation_sources_source_generation_id
            ON generation_sources(source_generation_id)
        """)

        print("Migration 107: Created generation_sources table")


def down():
    """Drop generation_sources table."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS generation_sources")
        print("Migration 107: Dropped generation_sources table")
