"""
Add memory_reflection toggle to LLM configurations
"""

from src.platform.database.database import db

def up():
    """Add memory_reflection column to llm_configurations table, default ON."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='llm_configurations'
        """)
        if not cursor.fetchone():
            return

        cursor.execute("PRAGMA table_info(llm_configurations)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'memory_reflection' not in columns:
            cursor.execute("""
                ALTER TABLE llm_configurations
                ADD COLUMN memory_reflection BOOLEAN NOT NULL DEFAULT 1
            """)

def down():
    """Remove memory_reflection from llm_configurations table."""
    # SQLite doesn't support DROP COLUMN directly; leaving the column on
    # rollback matches the precedent set by 031_add_llm_vision_support.
    pass
