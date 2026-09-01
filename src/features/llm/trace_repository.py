"""Persistence for chat LLM call traces (admin session-debug viewer).

Raw SQL over ``chat_llm_call_traces`` (migration 084), following the same
plain-repository shape as ``src.features.llm.repository.LLMConfigurationRepository``.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from src.platform.database.rows import json_column
from src.platform.util.ids import generate_ulid

RETENTION_DAYS = 7


def _dumps(value: Any) -> Optional[str]:
    return json.dumps(value) if value is not None else None


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "message_id": row["message_id"],
        "purpose": row["purpose"],
        "iteration": row["iteration"],
        "provider": row["provider"],
        "model": row["model"],
        "request_system": row["request_system"],
        "request_messages": json_column(row["request_messages"]) or [],
        "request_params": json_column(row["request_params"]) or {},
        "request_tools": json_column(row["request_tools"]),
        "response_text": row["response_text"],
        "response_tool_calls": json_column(row["response_tool_calls"]),
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "duration_ms": row["duration_ms"],
        "created_at": row["created_at"],
    }


class ChatCallTraceRepository:
    """CRUD access to ``chat_llm_call_traces``."""

    def create(
        self,
        *,
        session_id: str,
        user_id: Optional[str],
        purpose: str,
        iteration: int,
        provider: str,
        model: str,
        request_system: Optional[str],
        request_messages: List[Dict[str, Any]],
        request_params: Dict[str, Any],
        request_tools: Optional[Any],
        response_text: Optional[str],
        response_tool_calls: Optional[Any],
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        duration_ms: int,
    ) -> str:
        """Insert one call trace row. Returns the new row id."""
        trace_id = generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO chat_llm_call_traces (
                    id, session_id, user_id, message_id, purpose, iteration,
                    provider, model, request_system, request_messages,
                    request_params, request_tools, response_text,
                    response_tool_calls, prompt_tokens, completion_tokens,
                    duration_ms, created_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trace_id, session_id, user_id, purpose, iteration,
                provider, model, request_system, _dumps(request_messages),
                _dumps(request_params), _dumps(request_tools), response_text,
                _dumps(response_tool_calls), prompt_tokens, completion_tokens,
                duration_ms, datetime.now().isoformat(),
            ))
        return trace_id

    def backfill_message_id(self, session_id: str, message_id: str) -> int:
        """Stamp every un-attributed trace row of ``session_id`` with ``message_id``.

        Called once per turn right after the assistant message is persisted.
        Chat turns are processed serially per session (no concurrent sends for
        the same session), so "every NULL row for this session" is exactly
        "this turn's rows" in practice.
        """
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "UPDATE chat_llm_call_traces SET message_id = ? "
                "WHERE session_id = ? AND message_id IS NULL",
                (message_id, session_id),
            )
            return cursor.rowcount

    def list_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        """All call traces for a session, oldest first, grouped by message via message_id."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM chat_llm_call_traces WHERE session_id = ? "
                "ORDER BY created_at ASC, iteration ASC",
                (session_id,),
            )
            return [_row_to_dict(row) for row in cursor.fetchall()]

    def delete_for_session(self, session_id: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_llm_call_traces WHERE session_id = ?", (session_id,))
            return cursor.rowcount

    def delete_all(self) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_llm_call_traces")
            return cursor.rowcount

    def prune_older_than(self, days: int = RETENTION_DAYS) -> int:
        """Delete rows created before ``days`` ago. Returns the row count deleted."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_llm_call_traces WHERE created_at < ?", (cutoff,))
            return cursor.rowcount


# Process-wide singleton, matching the module-level repository instances in
# src.features.llm.repository (llm_repository, llm_config_repo, ...).
chat_call_trace_repository = ChatCallTraceRepository()
