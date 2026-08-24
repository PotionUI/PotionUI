"""
Migration: Create sessions table for saving user session data per preset
"""

from src.platform.database.database import db

def up():
    """Create the sessions table"""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                preset_id TEXT NOT NULL,
                name TEXT NOT NULL,
                data TEXT NOT NULL,  -- JSON string containing all session data
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
                UNIQUE(user_id, preset_id, name)  -- Unique session names per user per preset
            )
        """)
        
        # Create index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_user_preset 
            ON sessions (user_id, preset_id)
        """)