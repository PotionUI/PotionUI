"""
Generalize llm_memory's model-only scoping into a generic scope_ref.

Renames the `model_id` column to `scope_ref` so memory notes can be scoped to
any reference (preset id, model id, ...) rather than only models. Existing
model-scoped rows keep their model_id value as scope_ref; other rows get
scope_ref = NULL.
"""

from src.platform.database.database import db


def up():
    """Rename llm_memory.model_id to scope_ref (SQLite requires a table rebuild)."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(llm_memory)")
        columns = {row['name'] for row in cursor.fetchall()}
        if 'scope_ref' in columns:
            print("Migration 068: scope_ref already present, skipping")
            return

        cursor.execute("""
            CREATE TABLE llm_memory_new (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                content TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                scope_ref TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            INSERT INTO llm_memory_new (id, user_id, key, content, scope, scope_ref, created_at, updated_at)
            SELECT id, user_id, key, content, scope,
                   CASE WHEN scope = 'model' THEN model_id ELSE NULL END,
                   created_at, updated_at
            FROM llm_memory
        """)

        cursor.execute("DROP TABLE llm_memory")
        cursor.execute("ALTER TABLE llm_memory_new RENAME TO llm_memory")

        cursor.execute("""
            CREATE UNIQUE INDEX idx_llm_memory_key_scope
            ON llm_memory (user_id, key, scope, COALESCE(scope_ref, ''))
        """)
        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_scope
            ON llm_memory (user_id, scope)
        """)
        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_scope_ref
            ON llm_memory (user_id, scope, scope_ref)
        """)

        print("Migration 068: Renamed llm_memory.model_id to scope_ref")


def down():
    """Revert scope_ref back to model_id (drops any preset-scoped rows' ref)."""
    with db.get_cursor() as cursor:
        cursor.execute("PRAGMA table_info(llm_memory)")
        columns = {row['name'] for row in cursor.fetchall()}
        if 'model_id' in columns:
            print("Migration 068: model_id already present, skipping")
            return

        cursor.execute("""
            CREATE TABLE llm_memory_old (
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
            INSERT INTO llm_memory_old (id, user_id, key, content, scope, model_id, created_at, updated_at)
            SELECT id, user_id, key, content, scope,
                   CASE WHEN scope = 'model' THEN scope_ref ELSE NULL END,
                   created_at, updated_at
            FROM llm_memory
        """)

        cursor.execute("DROP TABLE llm_memory")
        cursor.execute("ALTER TABLE llm_memory_old RENAME TO llm_memory")

        cursor.execute("""
            CREATE UNIQUE INDEX idx_llm_memory_key_scope
            ON llm_memory (user_id, key, scope, COALESCE(model_id, ''))
        """)
        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_scope
            ON llm_memory (user_id, scope)
        """)
        cursor.execute("""
            CREATE INDEX idx_llm_memory_user_model
            ON llm_memory (user_id, model_id)
        """)

        print("Migration 068: Reverted llm_memory.scope_ref to model_id")
