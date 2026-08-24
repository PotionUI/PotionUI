"""Migration 079: reset the prompt domain around ordered rich segments.

This is intentionally destructive.  The prompt library was still a proof of
concept when this migration was introduced, so carrying the paired-prompt and
single-snippet template schemas forward would preserve contracts the product no
longer supports.
"""

from src.platform.database.database import db
from src.platform.util.ids import generate_ulid


DEFAULT_CATEGORIES = (
    ("Quality & Technical", "Quality enhancing and technical prompt fragments", "#10B981"),
    ("Art Style", "Artistic styles and aesthetic directions", "#8B5CF6"),
    ("Environment", "Lighting, atmosphere, and environments", "#F59E0B"),
    ("Composition", "Camera, framing, and composition", "#EF4444"),
)


def _drop_prompt_domain(cursor):
    # Children must be removed first while foreign keys are enabled.
    cursor.execute("DROP TABLE IF EXISTS generation_segment_autocomplete")
    cursor.execute("DROP TABLE IF EXISTS generation_segments")
    cursor.execute("DROP TABLE IF EXISTS segment_template_segments")
    cursor.execute("DROP TABLE IF EXISTS segment_templates")
    cursor.execute("DROP TABLE IF EXISTS saved_segments")
    cursor.execute("DROP TABLE IF EXISTS prompt_segments")
    cursor.execute("DROP TABLE IF EXISTS prompts")
    cursor.execute("DROP TABLE IF EXISTS model_prompts")
    cursor.execute("DROP TABLE IF EXISTS segment_categories")


def up():
    with db.get_cursor() as cursor:
        _drop_prompt_domain(cursor)

        cursor.execute("""
            CREATE TABLE segment_categories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                color TEXT NOT NULL DEFAULT '#3B82F6',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE (user_id, name)
            )
        """)

        cursor.execute("""
            CREATE TABLE prompts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT COLLATE NOCASE,
                flattened_text TEXT NOT NULL DEFAULT '',
                usage_hint TEXT CHECK (usage_hint IS NULL OR usage_hint IN ('positive', 'negative')),
                source_group_id TEXT,
                source_provider TEXT,
                source_id TEXT,
                source_url TEXT,
                model_id TEXT,
                model_name TEXT,
                base_model TEXT,
                cfg_scale REAL,
                steps INTEGER,
                sampler TEXT,
                width INTEGER,
                height INTEGER,
                heart_count INTEGER NOT NULL DEFAULT 0,
                like_count INTEGER NOT NULL DEFAULT 0,
                laugh_count INTEGER NOT NULL DEFAULT 0,
                cry_count INTEGER NOT NULL DEFAULT 0,
                comment_count INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '[]',
                nsfw INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                embedded INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("""
            CREATE UNIQUE INDEX idx_prompts_source
            ON prompts(user_id, source_provider, source_id, usage_hint)
            WHERE source_id IS NOT NULL AND usage_hint IS NOT NULL
        """)
        cursor.execute("CREATE INDEX idx_prompts_user_created ON prompts(user_id, created_at DESC)")
        cursor.execute("CREATE INDEX idx_prompts_flattened ON prompts(user_id, flattened_text)")
        cursor.execute("CREATE INDEX idx_prompts_model ON prompts(user_id, model_id)")

        cursor.execute("PRAGMA table_info(enhancement_feedback)")
        feedback_columns = {row["name"] for row in cursor.fetchall()}
        if "model_prompt_id" in feedback_columns and "prompt_id" not in feedback_columns:
            cursor.execute(
                "ALTER TABLE enhancement_feedback RENAME COLUMN model_prompt_id TO prompt_id"
            )
            # Old ids pointed at the discarded paired-prompt table.
            cursor.execute("UPDATE enhancement_feedback SET prompt_id = NULL")
        elif "prompt_id" in feedback_columns:
            cursor.execute("UPDATE enhancement_feedback SET prompt_id = NULL")

        cursor.execute("""
            CREATE TABLE prompt_segments (
                id TEXT PRIMARY KEY,
                prompt_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),
                content TEXT NOT NULL DEFAULT '',
                chips TEXT NOT NULL DEFAULT '{}',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                name TEXT,
                color TEXT,
                description TEXT,
                FOREIGN KEY (prompt_id) REFERENCES prompts(id) ON DELETE CASCADE,
                UNIQUE (prompt_id, position)
            )
        """)
        cursor.execute("CREATE INDEX idx_prompt_segments_parent ON prompt_segments(prompt_id, position)")

        cursor.execute("""
            CREATE TABLE saved_segments (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                category_id TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),
                content TEXT NOT NULL DEFAULT '',
                chips TEXT NOT NULL DEFAULT '{}',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                color TEXT,
                description TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (category_id) REFERENCES segment_categories(id) ON DELETE RESTRICT,
                UNIQUE (user_id, name)
            )
        """)
        cursor.execute("CREATE INDEX idx_saved_segments_category ON saved_segments(user_id, category_id)")

        cursor.execute("""
            CREATE TABLE segment_templates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE (user_id, name)
            )
        """)
        cursor.execute("""
            CREATE TABLE segment_template_segments (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                type TEXT NOT NULL DEFAULT 'content' CHECK (type IN ('content', 'break')),
                content TEXT NOT NULL DEFAULT '',
                chips TEXT NOT NULL DEFAULT '{}',
                is_enabled INTEGER NOT NULL DEFAULT 1,
                name TEXT,
                color TEXT,
                description TEXT,
                FOREIGN KEY (template_id) REFERENCES segment_templates(id) ON DELETE CASCADE,
                UNIQUE (template_id, position)
            )
        """)
        cursor.execute("CREATE INDEX idx_template_segments_parent ON segment_template_segments(template_id, position)")

        # Generation history keeps detached composition data only.  There are no
        # saved-prompt source fields because applying a library item is a copy.
        cursor.execute("""
            CREATE TABLE generation_segments (
                id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'positive'
                    CHECK (channel IN ('positive', 'negative')),
                prompt_index INTEGER NOT NULL DEFAULT 0,
                segment_index INTEGER NOT NULL DEFAULT 0,
                segment_type TEXT NOT NULL DEFAULT 'content'
                    CHECK (segment_type IN ('content', 'break')),
                text TEXT NOT NULL DEFAULT '',
                name TEXT,
                color TEXT,
                description TEXT,
                is_disabled INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE
            )
        """)
        cursor.execute("CREATE INDEX idx_generation_segments_generation ON generation_segments(generation_id)")
        cursor.execute("""
            CREATE TABLE generation_segment_autocomplete (
                id TEXT PRIMARY KEY,
                segment_id TEXT NOT NULL,
                generation_id TEXT NOT NULL,
                autocomplete_value_id TEXT,
                category_path TEXT,
                value TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (segment_id) REFERENCES generation_segments(id) ON DELETE CASCADE,
                FOREIGN KEY (generation_id) REFERENCES generations(id) ON DELETE CASCADE,
                FOREIGN KEY (autocomplete_value_id) REFERENCES autocomplete_values(id) ON DELETE SET NULL
            )
        """)
        cursor.execute("CREATE INDEX idx_generation_segment_autocomplete_segment ON generation_segment_autocomplete(segment_id)")
        cursor.execute("CREATE INDEX idx_generation_segment_autocomplete_generation ON generation_segment_autocomplete(generation_id)")
        cursor.execute("CREATE INDEX idx_generation_segment_autocomplete_value ON generation_segment_autocomplete(autocomplete_value_id)")

        cursor.execute("SELECT id FROM users")
        for row in cursor.fetchall():
            for name, description, color in DEFAULT_CATEGORIES:
                cursor.execute(
                    """INSERT INTO segment_categories
                       (id, user_id, name, description, color)
                       VALUES (?, ?, ?, ?, ?)""",
                    (generate_ulid(), row["id"], name, description, color),
                )


def down():
    # This reset has no data-preserving inverse.  Recreate the immediately prior
    # tables by re-running migrations 049 and 065 if a development database must
    # be rolled back.
    with db.get_cursor() as cursor:
        _drop_prompt_domain(cursor)
