"""
Migration 056: Create enhancement_feedback table
Stores user approve/reject verdicts on prompts proposed by the enhancement pipeline.
"""

from src.platform.database.database import db


def up():
    """Create enhancement_feedback table"""
    with db.get_cursor() as cursor:
        # Check if table already exists (idempotent)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='enhancement_feedback'")
        if cursor.fetchone():
            print("Migration 056: enhancement_feedback table already exists, skipping")
            return

        cursor.execute("""
            CREATE TABLE enhancement_feedback (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                verdict TEXT NOT NULL,
                model_id TEXT,
                reason TEXT,
                model_prompt_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX idx_enhancement_feedback_user_model_verdict
            ON enhancement_feedback(user_id, model_id, verdict)
        """)

        cursor.execute("""
            CREATE INDEX idx_enhancement_feedback_message
            ON enhancement_feedback(message_id)
        """)

        print("Migration 056: Created enhancement_feedback table with indexes")


def down():
    """Drop enhancement_feedback table"""
    with db.get_cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS enhancement_feedback")
        print("Migration 056: Dropped enhancement_feedback table")
