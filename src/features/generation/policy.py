"""Access-control policy for generations.

A generation belongs to the user who started it. Both the live
(in-memory status tracker) and historical (database) views expose the
owning `user_id`, so the same rule governs every read/mutation path:
only the owner or an administrator may touch a generation.
"""
from typing import Any, Optional

from src.platform.security.user import AccountType


class GenerationAccessDenied(Exception):
    """Raised when a user attempts to access a generation they do not own."""


class GenerationPolicy:
    """Ownership checks for generations (live records and history rows)."""

    @staticmethod
    def _is_admin(user: Any) -> bool:
        return getattr(user, "account_type", None) == AccountType.ADMIN

    @staticmethod
    def can_access(user: Any, owner_id: Optional[str]) -> bool:
        """Return True if `user` may access a generation owned by `owner_id`.

        Administrators may access anything. A generation with an unknown
        owner (`owner_id is None`) is only accessible to administrators —
        we never expose ownerless records to regular users.
        """
        if user is None:
            return False
        if GenerationPolicy._is_admin(user):
            return True
        if owner_id is None:
            return False
        return getattr(user, "id", None) == owner_id

    @staticmethod
    def assert_can_access(user: Any, owner_id: Optional[str]) -> None:
        """Raise GenerationAccessDenied if `user` may not access the generation."""
        if not GenerationPolicy.can_access(user, owner_id):
            raise GenerationAccessDenied(
                "User is not permitted to access this generation"
            )
