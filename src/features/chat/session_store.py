"""Chat session lifecycle store.

Owns session CRUD plus the accept/reject state transitions and the two
"fetch-or-raise" lookups the rest of the chat feature depends on. Extracted
from the ChatRuntime coordinator so session bookkeeping is separable from
message/LLM orchestration.
"""

import logging
from typing import List, Optional, Tuple

from src.features.chat.dto import SessionResponse
from src.features.chat.exceptions import (
    SessionNotFoundException,
    AccessDeniedException,
    SessionCreationFailedException,
)
from src.features.chat.hooks import CHAT_SESSION_HOOKS

logger = logging.getLogger(__name__)


class ChatSessionStore:
    """Session CRUD, accept/reject and the shared fetch-or-raise lookups."""

    def __init__(self, manager):
        self._m = manager

    # --- Lookups ---

    def get_or_raise(self, session_id: str) -> SessionResponse:
        """Get session by ID or raise SessionNotFoundException.

        Raises:
            SessionNotFoundException: If session not found
        """
        session = self._m.chat_repository.get_session(session_id)
        if not session:
            raise SessionNotFoundException(f"Session {session_id} not found")
        return session

    def get_with_messages_or_raise(self, session_id: str) -> SessionResponse:
        """Get session with messages by ID or raise SessionNotFoundException.

        Raises:
            SessionNotFoundException: If session not found
        """
        session = self._m.chat_repository.get_session_with_messages(session_id)
        if not session:
            raise SessionNotFoundException(f"Session {session_id} not found")
        return session

    # --- CRUD ---

    def create_session(
        self,
        user_id: str,
        original_text: Optional[str] = None,
        llm_config_id: Optional[str] = None,
        mode: str = 'generation',
        name: Optional[str] = None,
        system_message: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
    ) -> SessionResponse:
        """Create a new chat session.

        Executes hooks:
        - chat.session.before_create: Can modify/validate session data or block
        - chat.session.after_create: Notification of successful creation

        Args:
            user_id: ID of the user creating the session
            original_text: Optional original text context
            llm_config_id: Optional LLM configuration ID
            mode: Chat mode id (immutable for the session's lifetime)
            name: Optional session name
            system_message: Optional custom system message
            enabled_tools: Subtractive tool filter within the mode's tools (None = all)

        Returns:
            Created SessionResponse

        Raises:
            UnknownChatModeException: If the mode is not registered
            SessionCreationFailedException: If creation fails or is blocked
        """
        # Validate the mode before anything else
        self._m.chat_mode_registry.require(mode)

        # Execute before_create hook
        hook_data, blocked, _ctx = self._m._execute_hook(
            CHAT_SESSION_HOOKS.before_create,
            {
                "user_id": user_id,
                "mode": mode,
                "llm_config_id": llm_config_id,
                "original_text": original_text,
                "name": name,
                "system_message": system_message
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Session creation blocked")
            logger.warning(f"Session creation blocked by plugin: {reason}")
            raise SessionCreationFailedException(reason)

        # Allow hooks to modify data (a hook-modified mode is re-validated)
        hook_mode = hook_data.get("mode", mode)
        if hook_mode != mode:
            self._m.chat_mode_registry.require(hook_mode)
            mode = hook_mode
        llm_config_id = hook_data.get("llm_config_id", llm_config_id)
        name = hook_data.get("name", name)
        system_message = hook_data.get("system_message", system_message)

        # Store system_message and enabled_tools in metadata
        metadata = {}
        if system_message:
            metadata['system_message'] = system_message
        if enabled_tools is not None:
            metadata['enabled_tools'] = enabled_tools

        # Create session via repository
        session = self._m.chat_repository.create_session(
            user_id=user_id,
            original_text=original_text,
            llm_config_id=llm_config_id,
            mode=mode,
            name=name,
            metadata=metadata if metadata else None
        )

        if not session:
            raise SessionCreationFailedException("Failed to create chat session")

        # Execute after_create hook
        self._m._execute_hook(
            CHAT_SESSION_HOOKS.after_create,
            {
                "session_id": session.id,
                "user_id": user_id,
                "mode": mode
            }
        )

        logger.info(f"Chat session created: {session.id} for user {user_id} (mode: {mode})")
        return session

    def list_sessions(
        self,
        user_id: str,
        mode: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0
    ) -> Tuple[List[SessionResponse], int]:
        """List sessions for a user, most recent first.

        Returns:
            Tuple of (sessions page, total matching count)
        """
        return self._m.chat_repository.list_sessions(
            user_id=user_id,
            mode=mode,
            search=search,
            limit=limit,
            offset=offset,
        )

    def get_session(self, session_id: str, user_id: str) -> SessionResponse:
        """Get a session with all messages.

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
        """
        session = self.get_with_messages_or_raise(session_id)
        self._m._verify_ownership(session, user_id)
        return session

    def update_session(
        self,
        session_id: str,
        user_id: str,
        name: Optional[str] = None,
        llm_config_id: Optional[str] = None,
    ) -> SessionResponse:
        """Update session properties.

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
        """
        session = self.get_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        updated = None

        if name is not None:
            updated = self._m.chat_repository.update_session_name(session_id, name)
            if not updated:
                raise SessionCreationFailedException("Failed to update session name")

        if llm_config_id is not None:
            updated = self._m.chat_repository.update_session_llm_config(session_id, llm_config_id)
            if not updated:
                raise SessionCreationFailedException("Failed to update session LLM configuration")

        # Return current session if no changes
        return updated or session

    def delete_session(self, session_id: str, user_id: str) -> bool:
        """Delete a session and all its messages.

        Executes hooks:
        - chat.session.before_delete: Can block deletion
        - chat.session.after_delete: Notification of successful deletion

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own the session
        """
        session = self.get_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        # Execute before_delete hook
        hook_data, blocked, _ctx = self._m._execute_hook(
            CHAT_SESSION_HOOKS.before_delete,
            {
                "session_id": session_id,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Deletion blocked")
            logger.warning(f"Session deletion blocked by plugin: {reason}")
            raise AccessDeniedException(reason)

        success = self._m.chat_repository.delete_session(session_id)

        if success:
            # Execute after_delete hook
            self._m._execute_hook(
                CHAT_SESSION_HOOKS.after_delete,
                {
                    "session_id": session_id,
                    "user_id": user_id
                }
            )
            logger.info(f"Chat session deleted: {session_id}")

        return success

    # --- State transitions ---

    def accept_session(self, session_id: str, user_id: str) -> bool:
        """Accept the session's suggestion and close it.

        Executes hooks:
        - chat.session.before_accept: Can validate or block
        - chat.session.after_accept: Notification of acceptance

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own or blocked
        """
        session = self.get_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        # Get final text from last assistant message if available
        messages = self._m.chat_repository.get_messages(session_id)
        final_text = None
        if messages:
            assistant_msgs = [m for m in messages if m.role == 'assistant']
            if assistant_msgs:
                final_text = assistant_msgs[-1].content

        # Execute before_accept hook
        hook_data, blocked, _ctx = self._m._execute_hook(
            CHAT_SESSION_HOOKS.before_accept,
            {
                "session_id": session_id,
                "user_id": user_id,
                "final_text": final_text
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Acceptance blocked")
            logger.warning(f"Session acceptance blocked by plugin: {reason}")
            raise AccessDeniedException(reason)

        success = self._m.chat_repository.accept_session(session_id)

        if success:
            # Execute after_accept hook
            self._m._execute_hook(
                CHAT_SESSION_HOOKS.after_accept,
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "final_text": final_text
                }
            )
            logger.info(f"Chat session accepted: {session_id}")

        return success

    def reject_session(self, session_id: str, user_id: str) -> bool:
        """Reject the session's suggestion and close it.

        Executes hooks:
        - chat.session.before_reject: Can validate or block
        - chat.session.after_reject: Notification of rejection

        Raises:
            SessionNotFoundException: If session not found
            AccessDeniedException: If user doesn't own or blocked
        """
        session = self.get_or_raise(session_id)
        self._m._verify_ownership(session, user_id)

        # Execute before_reject hook
        hook_data, blocked, _ctx = self._m._execute_hook(
            CHAT_SESSION_HOOKS.before_reject,
            {
                "session_id": session_id,
                "user_id": user_id
            }
        )

        if blocked:
            reason = hook_data.get("block_reason", "Rejection blocked")
            logger.warning(f"Session rejection blocked by plugin: {reason}")
            raise AccessDeniedException(reason)

        success = self._m.chat_repository.reject_session(session_id)

        if success:
            # Execute after_reject hook
            self._m._execute_hook(
                CHAT_SESSION_HOOKS.after_reject,
                {
                    "session_id": session_id,
                    "user_id": user_id
                }
            )
            logger.info(f"Chat session rejected: {session_id}")

        return success
