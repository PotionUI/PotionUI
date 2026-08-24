"""Chat session access and validation policy.

Near-pure ownership/state checks pulled out of the ChatManager coordinator so
the session store, conversation runner and tool dispatcher can share one place
that decides who may touch a session.
"""

from src.features.chat.dto import SessionResponse
from src.features.chat.exceptions import (
    AccessDeniedException,
    SessionClosedException,
)


class ChatPolicy:
    """Ownership and active-state checks for chat sessions."""

    def __init__(self, manager):
        self._m = manager

    def verify_ownership(self, session: SessionResponse, user_id: str) -> None:
        """Verify user owns the session.

        Raises:
            AccessDeniedException: If user doesn't own the session
        """
        if session.user_id != user_id:
            raise AccessDeniedException("You don't have access to this session")

    def verify_active(self, session: SessionResponse) -> None:
        """Verify session is active (not closed).

        Raises:
            SessionClosedException: If session is not active
        """
        if session.status != 'active':
            raise SessionClosedException("Cannot perform operation on closed session")
