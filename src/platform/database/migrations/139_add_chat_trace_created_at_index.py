"""Index chat_llm_call_traces.created_at for the retention prune.

The existing idx_chat_llm_call_traces_session index is (session_id,
created_at) — no help to a DELETE that filters on created_at alone across
all sessions, so that prune would table-scan without this index.
"""

from src.platform.database.database import db


def up():
    with db.get_cursor() as cursor:
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_llm_call_traces_created_at "
            "ON chat_llm_call_traces(created_at)"
        )


def down():
    with db.get_cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS idx_chat_llm_call_traces_created_at")
