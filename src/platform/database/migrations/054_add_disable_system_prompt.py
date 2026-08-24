"""
Add disable_system_prompt option to LLM configurations
"""

from src.platform.database.database import db

def up():
    """Add disable_system_prompt column to llm_configurations table"""
    with db.get_cursor() as cursor:
        # Check if llm_configurations table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='llm_configurations'
        """)
        if not cursor.fetchone():
            return

        # Check if column already exists
        cursor.execute("PRAGMA table_info(llm_configurations)")
        columns = [col[1] for col in cursor.fetchall()]

        if 'disable_system_prompt' not in columns:
            cursor.execute("""
                ALTER TABLE llm_configurations
                ADD COLUMN disable_system_prompt BOOLEAN NOT NULL DEFAULT 0
            """)

def down():
    """Remove disable_system_prompt from llm_configurations table"""
    # SQLite doesn't support DROP COLUMN directly
    pass
