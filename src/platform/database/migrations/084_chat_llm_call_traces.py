"""
Admin session-debug viewer: persist every LLM call made during a chat turn so
an admin can see exactly what was sent, where, by whom, per turn.

One row per wire call to a provider client (openai.py / ollama.py) — a single
chat turn may produce several rows when the tool-calling loop iterates.
``message_id`` starts NULL and is backfilled with the assistant message id once
the turn finishes persisting (ConversationRunner does this after the message
row exists). Recording is gated by the ``chat_llm_call_tracing`` setting
(default ON), read by ChatCallTraceRecorder.
"""

from src.platform.database.database import db


def up():
    """Create chat_llm_call_traces and seed the chat_llm_call_tracing setting."""
    with db.get_cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_llm_call_traces (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT,
                message_id TEXT,
                purpose TEXT NOT NULL DEFAULT 'chat',
                iteration INTEGER NOT NULL DEFAULT 1,
                provider TEXT,
                model TEXT,
                request_system TEXT,
                request_messages TEXT,
                request_params TEXT,
                request_tools TEXT,
                response_text TEXT,
                response_tool_calls TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                duration_ms INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_llm_call_traces_session "
            "ON chat_llm_call_traces(session_id, created_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_llm_call_traces_message "
            "ON chat_llm_call_traces(message_id)"
        )

        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name='settings'
        """)
        if not cursor.fetchone():
            # Settings table doesn't exist yet (fresh install ordering) — skip seeding,
            # the setting reads back its default via SettingsManager.get_setting().
            return

        import random
        import string
        import time

        timestamp = int(time.time() * 1000)
        randomness = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
        simple_id = f"{timestamp:013d}{randomness}"

        description = (
            "Persist every LLM call made during chat (exact request/response, "
            "provider/model, tokens, timing) for the admin session-debug viewer"
        )
        cursor.execute("""
            INSERT OR IGNORE INTO settings (
                id, key, value, value_type, description, type
            ) VALUES (?, 'chat_llm_call_tracing', 'true', 'boolean', ?, 'SYSTEM')
        """, [simple_id, description])


def down():
    """Drop chat_llm_call_traces and its setting."""
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_chat_llm_call_traces_session")
        cursor.execute("DROP INDEX IF EXISTS idx_chat_llm_call_traces_message")
        cursor.execute("DROP TABLE IF EXISTS chat_llm_call_traces")
        cursor.execute("DELETE FROM settings WHERE key = 'chat_llm_call_tracing'")
