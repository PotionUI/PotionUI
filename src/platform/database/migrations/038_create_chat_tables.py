"""
Create chat tables for persistent conversational chat system
"""

from src.platform.database.database import db


def up():
    """Create chat tables"""
    with db.get_cursor() as cursor:
        # Create chat_sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_type TEXT NOT NULL DEFAULT 'segments',
                name TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                llm_config_id TEXT,
                style_id TEXT,
                original_text TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)

        # Create chat_messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                parsed_content TEXT,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
        """)

        # Create triggers to update updated_at on chat_sessions
        cursor.execute("""
            CREATE TRIGGER IF NOT EXISTS update_chat_sessions_updated_at
            AFTER UPDATE ON chat_sessions
            FOR EACH ROW
            BEGIN
                UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END
        """)

        # Create indices for performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, session_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_status ON chat_sessions(user_id, status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_created ON chat_sessions(created_at DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(session_id, created_at)")


def down():
    """Drop chat tables"""
    with db.get_cursor() as cursor:
        # Drop triggers
        cursor.execute("DROP TRIGGER IF EXISTS update_chat_sessions_updated_at")

        # Drop indices
        cursor.execute("DROP INDEX IF EXISTS idx_chat_sessions_user")
        cursor.execute("DROP INDEX IF EXISTS idx_chat_sessions_status")
        cursor.execute("DROP INDEX IF EXISTS idx_chat_sessions_created")
        cursor.execute("DROP INDEX IF EXISTS idx_chat_messages_session")
        cursor.execute("DROP INDEX IF EXISTS idx_chat_messages_created")

        # Drop tables (messages first due to FK constraint)
        cursor.execute("DROP TABLE IF EXISTS chat_messages")
        cursor.execute("DROP TABLE IF EXISTS chat_sessions")
