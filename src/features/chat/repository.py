from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import json
import logging
from src.platform.database import db
from src.features.chat.records import ChatSession, ChatMessage
from src.features.chat.dto import SessionResponse, MessageResponse
from src.platform.util.ids import generate_ulid

logger = logging.getLogger(__name__)


def _message_to_dto(message: ChatMessage) -> MessageResponse:
    """Convert internal ChatMessage to MessageResponse DTO"""
    metadata = message.metadata or {}
    return MessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        parsed_content=message.parsed_content,
        created_at=message.created_at.isoformat() if message.created_at else None,
        tokens_used=metadata.get('tokens_used'),
        prompt_tokens=metadata.get('prompt_tokens'),
        completion_tokens=metadata.get('completion_tokens'),
        tool_executions=metadata.get('tool_executions'),
        metadata=message.metadata,
    )


def _session_to_dto(
    session: ChatSession,
    include_messages: bool = False,
    message_count: Optional[int] = None
) -> SessionResponse:
    """Convert internal ChatSession to SessionResponse DTO"""
    messages = None
    if include_messages and session.messages:
        messages = [_message_to_dto(msg) for msg in session.messages]

    if message_count is None:
        message_count = len(session.messages) if session.messages else 0

    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        mode=session.mode,
        name=session.name,
        status=session.status,
        llm_config_id=session.llm_config_id,
        original_text=session.original_text,
        title_generated=session.title_generated,
        created_at=session.created_at.isoformat() if session.created_at else None,
        updated_at=session.updated_at.isoformat() if session.updated_at else None,
        closed_at=session.closed_at.isoformat() if session.closed_at else None,
        message_count=message_count,
        messages=messages,
        metadata=session.metadata,
    )


class ChatMessageRepository:
    """Low-level repository for chat message operations"""

    def _get_by_id_internal(self, message_id: str) -> Optional[ChatMessage]:
        """Get a message by ID (internal model)"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
            row = cursor.fetchone()
            return ChatMessage.from_row(row) if row else None

    def get_by_id(self, message_id: str) -> Optional[MessageResponse]:
        """Get a message by ID"""
        message = self._get_by_id_internal(message_id)
        return _message_to_dto(message) if message else None

    def _get_by_session_internal(self, session_id: str) -> List[ChatMessage]:
        """Get all messages for a session (internal models)"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,)
            )
            return [ChatMessage.from_row(row) for row in cursor.fetchall()]

    def get_by_session(self, session_id: str) -> List[MessageResponse]:
        """Get all messages for a session ordered by creation time"""
        messages = self._get_by_session_internal(session_id)
        return [_message_to_dto(msg) for msg in messages]

    def create(self, message: ChatMessage) -> Optional[MessageResponse]:
        """Create a new message, returns DTO"""
        try:
            now = datetime.now()
            message.created_at = now
            parsed_content_json = json.dumps(message.parsed_content) if message.parsed_content else None
            metadata_json = json.dumps(message.metadata) if message.metadata else None

            with db.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO chat_messages
                    (id, session_id, role, content, parsed_content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    message.id, message.session_id, message.role, message.content,
                    parsed_content_json, metadata_json, now.isoformat()
                ))
            return _message_to_dto(message)
        except Exception:
            logger.exception("Failed to create chat message %s", message.id)
            return None

    def delete_by_session(self, session_id: str) -> bool:
        """Delete all messages for a session"""
        with db.get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            return True

    def count_by_session(self, session_id: str) -> int:
        """Count messages in a session"""
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
                (session_id,)
            )
            result = cursor.fetchone()
            return result[0] if result else 0


class ChatSessionRepository:
    """Low-level repository for chat session operations"""

    def __init__(self):
        self.message_repo = ChatMessageRepository()

    def _get_by_id_internal(self, session_id: str) -> Optional[ChatSession]:
        """Get a session by ID (internal model)"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            return ChatSession.from_row(row) if row else None

    def get_by_id(self, session_id: str) -> Optional[SessionResponse]:
        """Get a session by ID without messages"""
        session = self._get_by_id_internal(session_id)
        return _session_to_dto(session) if session else None

    def _get_with_messages_internal(self, session_id: str) -> Optional[ChatSession]:
        """Get a session by ID with messages (internal model)"""
        session = self._get_by_id_internal(session_id)
        if session:
            messages = self.message_repo._get_by_session_internal(session_id)
            session.messages = messages
        return session

    def get_with_messages(self, session_id: str) -> Optional[SessionResponse]:
        """Get a session by ID with all messages"""
        session = self._get_with_messages_internal(session_id)
        return _session_to_dto(session, include_messages=True) if session else None

    def list_sessions(
        self,
        user_id: str,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Tuple[List[SessionResponse], int]:
        """List sessions for a user, most recent first, with total count.

        Args:
            user_id: The owning user
            mode: Optional chat mode filter
            search: Optional case-insensitive substring match on the session name
            status: Optional status filter
            limit: Page size
            offset: Page offset

        Returns:
            Tuple of (sessions page, total matching count)
        """
        where = ["user_id = ?"]
        params: List[Any] = [user_id]
        if mode:
            where.append("mode = ?")
            params.append(mode)
        if status:
            where.append("status = ?")
            params.append(status)
        if search:
            where.append("name LIKE ?")
            params.append(f"%{search}%")

        where_sql = " AND ".join(where)

        with db.get_cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM chat_sessions WHERE {where_sql}",
                tuple(params)
            )
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT s.*, (
                    SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id
                ) AS message_count
                FROM chat_sessions s
                WHERE {where_sql}
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
            """, tuple(params + [limit, offset]))

            sessions = []
            for row in cursor.fetchall():
                session = ChatSession.from_row(row)
                sessions.append(_session_to_dto(session, message_count=row['message_count']))
            return sessions, total

    def list_sessions_admin(
        self,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List sessions across ALL users for the admin session-debug viewer.

        Returns dicts (not SessionResponse) since each row carries the owning
        user's username/email alongside the session fields.
        """
        where = []
        params: List[Any] = []
        if search:
            where.append("(s.name LIKE ? OR u.username LIKE ? OR u.email LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        with db.get_cursor() as cursor:
            cursor.execute(f"""
                SELECT COUNT(*) FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                {where_sql}
            """, tuple(params))
            total = cursor.fetchone()[0]

            cursor.execute(f"""
                SELECT
                    s.id, s.user_id, s.mode, s.name, s.status, s.llm_config_id,
                    s.created_at, s.updated_at,
                    u.username, u.email,
                    (SELECT COUNT(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
                FROM chat_sessions s
                JOIN users u ON u.id = s.user_id
                {where_sql}
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
            """, tuple(params + [limit, offset]))

            sessions = [dict(row) for row in cursor.fetchall()]
            return sessions, total

    def create(self, session: ChatSession) -> Optional[SessionResponse]:
        """Create a new session, returns DTO"""
        try:
            now = datetime.now()
            session.created_at = now
            session.updated_at = now
            metadata_json = json.dumps(session.metadata) if session.metadata else None

            with db.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO chat_sessions
                    (id, user_id, mode, name, status, llm_config_id,
                     original_text, title_generated, metadata, created_at, updated_at, closed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session.id, session.user_id, session.mode, session.name,
                    session.status, session.llm_config_id,
                    session.original_text, int(session.title_generated), metadata_json,
                    now.isoformat(), now.isoformat(),
                    session.closed_at.isoformat() if session.closed_at else None
                ))
            return _session_to_dto(session)
        except Exception:
            logger.exception("Failed to create chat session %s", session.id)
            return None

    def update(self, session_id: str, **kwargs) -> bool:
        """Update session fields"""
        if not kwargs:
            return True

        try:
            # Build dynamic update query. 'mode' is intentionally absent: the
            # chat mode is immutable for a session's lifetime.
            allowed_fields = ['name', 'status', 'llm_config_id', 'metadata',
                              'closed_at', 'updated_at', 'title_generated']
            update_parts = []
            values = []

            # Always update updated_at
            kwargs['updated_at'] = datetime.now()

            for field, value in kwargs.items():
                if field in allowed_fields:
                    if field == 'metadata' and value is not None:
                        value = json.dumps(value)
                    elif field in ('closed_at', 'updated_at') and value is not None and isinstance(value, datetime):
                        value = value.isoformat()
                    update_parts.append(f"{field} = ?")
                    values.append(value)

            if not update_parts:
                return True

            values.append(session_id)
            update_sql = f"UPDATE chat_sessions SET {', '.join(update_parts)} WHERE id = ?"

            with db.get_cursor() as cursor:
                cursor.execute(update_sql, tuple(values))
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to update chat session %s", session_id)
            return False

    def update_status(self, session_id: str, status: str, close: bool = False) -> bool:
        """Update session status and optionally close it"""
        if close:
            return self.update(session_id, status=status, closed_at=datetime.now())
        return self.update(session_id, status=status)

    def update_name(self, session_id: str, name: str) -> Optional[SessionResponse]:
        """Update session name and return updated session"""
        if self.update(session_id, name=name):
            return self.get_by_id(session_id)
        return None

    def set_title(self, session_id: str, name: str) -> Optional[SessionResponse]:
        """Set the LLM-generated title and mark the session as titled."""
        if self.update(session_id, name=name, title_generated=1):
            return self.get_by_id(session_id)
        return None

    def delete(self, session_id: str) -> bool:
        """Delete a session and all its messages"""
        with db.get_cursor() as cursor:
            # Messages are deleted via CASCADE
            cursor.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def exists(self, session_id: str) -> bool:
        """Check if session exists"""
        with db.get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,))
            return cursor.fetchone() is not None


class ChatRepository:
    """High-level repository providing business logic for chat operations"""

    def __init__(self):
        self.session_repo = ChatSessionRepository()
        self.message_repo = ChatMessageRepository()

    def create_session(
        self,
        user_id: str,
        original_text: Optional[str] = None,
        llm_config_id: Optional[str] = None,
        mode: str = 'generation',
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[SessionResponse]:
        """Create a new chat session"""
        session = ChatSession(
            id=generate_ulid(),
            user_id=user_id,
            mode=mode,
            name=name or self._generate_session_name(original_text),
            status='active',
            llm_config_id=llm_config_id,
            original_text=original_text,
            # A user-provided name counts as a title; placeholder names get
            # replaced by the LLM-generated title after the first exchange.
            title_generated=name is not None,
            metadata=metadata
        )

        return self.session_repo.create(session)

    def get_session(self, session_id: str) -> Optional[SessionResponse]:
        """Get a session by ID without messages"""
        return self.session_repo.get_by_id(session_id)

    def get_session_with_messages(self, session_id: str) -> Optional[SessionResponse]:
        """Get a session by ID with all messages"""
        return self.session_repo.get_with_messages(session_id)

    def list_sessions(
        self,
        user_id: str,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Tuple[List[SessionResponse], int]:
        """List sessions for the history view with filtering and pagination."""
        return self.session_repo.list_sessions(
            user_id=user_id,
            mode=mode,
            search=search,
            status=status,
            limit=limit,
            offset=offset,
        )

    def list_sessions_admin(
        self,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List sessions across ALL users (admin session-debug viewer)."""
        return self.session_repo.list_sessions_admin(search=search, limit=limit, offset=offset)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        parsed_content: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[MessageResponse]:
        """Add a message to a session"""
        if not self.session_repo.exists(session_id):
            return None

        message = ChatMessage(
            id=generate_ulid(),
            session_id=session_id,
            role=role,
            content=content,
            parsed_content=parsed_content,
            metadata=metadata
        )

        return self.message_repo.create(message)

    def get_messages(self, session_id: str) -> List[MessageResponse]:
        """Get all messages for a session"""
        return self.message_repo.get_by_session(session_id)

    def count_messages(self, session_id: str) -> int:
        """Count messages in a session"""
        return self.message_repo.count_by_session(session_id)

    def get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        """Get conversation history in format suitable for LLM API"""
        messages = self.message_repo._get_by_session_internal(session_id)
        return [
            {'role': msg.role, 'content': msg.content}
            for msg in messages
        ]

    def accept_session(self, session_id: str) -> bool:
        """Mark session as accepted and close it"""
        return self.session_repo.update_status(session_id, 'accepted', close=True)

    def reject_session(self, session_id: str) -> bool:
        """Mark session as rejected and close it"""
        return self.session_repo.update_status(session_id, 'rejected', close=True)

    def update_session_name(self, session_id: str, name: str) -> Optional[SessionResponse]:
        """Update the session name"""
        return self.session_repo.update_name(session_id, name)

    def update_session_llm_config(self, session_id: str, llm_config_id: str) -> Optional[SessionResponse]:
        """Rebind the session to a different LLM configuration."""
        if not self.session_repo.update(session_id, llm_config_id=llm_config_id):
            return None
        return self.session_repo.get_by_id(session_id)

    def set_session_title(self, session_id: str, name: str) -> Optional[SessionResponse]:
        """Set the LLM-generated title and mark the session as titled."""
        return self.session_repo.set_title(session_id, name)

    def record_memory_reflection(self, session_id: str, message_id: str) -> bool:
        """Merge reflected-up-to bookkeeping into session metadata.

        Read-modify-write so this never clobbers the other keys already living
        in session metadata (``system_message``, ``enabled_tools``).
        """
        session = self.get_session(session_id)
        if not session:
            return False
        metadata = dict(session.metadata or {})
        metadata['memory_reflection'] = {'reflected_up_to_message_id': message_id}
        return self.session_repo.update(session_id, metadata=metadata)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages"""
        return self.session_repo.delete(session_id)

    def get_message(self, message_id: str) -> Optional[MessageResponse]:
        """Get a single message by ID"""
        return self.message_repo.get_by_id(message_id)

    def update_message_metadata(self, message_id: str, metadata: Dict[str, Any]) -> bool:
        """Update the metadata of a message in-place.

        Returns True if the row was found and updated, False otherwise.
        """
        try:
            metadata_json = json.dumps(metadata)
            with db.get_cursor() as cursor:
                cursor.execute(
                    "UPDATE chat_messages SET metadata = ? WHERE id = ?",
                    (metadata_json, message_id)
                )
                return cursor.rowcount > 0
        except Exception:
            logger.exception("Failed to update metadata for message %s", message_id)
            return False

    def _generate_session_name(self, original_text: Optional[str]) -> str:
        """Generate a default name for a session based on original text"""
        if original_text:
            # Take first 50 chars of the original text
            name = original_text[:50].strip()
            if len(original_text) > 50:
                name += "..."
            return name
        return f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"


# Global repository instances
chat_message_repo = ChatMessageRepository()
chat_session_repo = ChatSessionRepository()
chat_repository = ChatRepository()
