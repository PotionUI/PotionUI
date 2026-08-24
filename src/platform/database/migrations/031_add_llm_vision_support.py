"""
Add vision support to LLM configurations
"""

from src.platform.database.database import db

def up():
    """Add supports_vision column to llm_configurations table"""
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

        if 'supports_vision' not in columns:
            # Add supports_vision column
            cursor.execute("""
                ALTER TABLE llm_configurations
                ADD COLUMN supports_vision BOOLEAN NOT NULL DEFAULT 0
            """)

        # Create index for vision-enabled configurations
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_llm_configurations_vision
            ON llm_configurations(supports_vision)
        """)

def down():
    """Remove vision support from llm_configurations table"""
    with db.get_cursor() as cursor:
        # Drop index
        cursor.execute("DROP INDEX IF EXISTS idx_llm_configurations_vision")

        # Remove column (SQLite requires table recreation for column removal)
        # For simplicity, we'll leave the column but you could recreate the table if needed
        # cursor.execute("ALTER TABLE llm_configurations DROP COLUMN supports_vision")
        # Note: SQLite doesn't support DROP COLUMN directly, would need table recreation
