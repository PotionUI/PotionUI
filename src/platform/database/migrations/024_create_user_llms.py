"""
Create user_llms table for tracking user-specific LLM assignments
"""

from src.platform.database.database import db

def up():
    """Create user_llms table"""
    with db.get_cursor() as cursor:
        # Create user_llms relation table (tracks which users have access to which LLM configurations)
        cursor.execute("""
            CREATE TABLE user_llms (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                llm_config_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                FOREIGN KEY (llm_config_id) REFERENCES llm_configurations (id) ON DELETE CASCADE,
                UNIQUE(user_id, llm_config_id)
            )
        """)

        # Create indexes for user_llms table
        cursor.execute("CREATE INDEX idx_user_llms_user_id ON user_llms (user_id)")
        cursor.execute("CREATE INDEX idx_user_llms_llm_config_id ON user_llms (llm_config_id)")
        cursor.execute("CREATE INDEX idx_user_llms_assigned_at ON user_llms (assigned_at)")

        # Create trigger to update updated_at
        cursor.execute("""
            CREATE TRIGGER update_user_llms_updated_at
            AFTER UPDATE ON user_llms
            FOR EACH ROW
            BEGIN
                UPDATE user_llms SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

def down():
    """Drop user_llms table"""
    with db.get_cursor() as cursor:
        # Drop trigger
        cursor.execute("DROP TRIGGER IF EXISTS update_user_llms_updated_at")

        # Drop indexes
        cursor.execute("DROP INDEX IF EXISTS idx_user_llms_assigned_at")
        cursor.execute("DROP INDEX IF EXISTS idx_user_llms_llm_config_id")
        cursor.execute("DROP INDEX IF EXISTS idx_user_llms_user_id")

        # Drop table
        cursor.execute("DROP TABLE IF EXISTS user_llms")