"""The user store as authentication sees it.

`Auth` is handed a user store; it never constructs one. The concrete
repository is a feature (`src.features.users.repository`), and platform code
may not reach into a feature, so the dependency is expressed structurally:
anything with these methods can back authentication.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, Tuple

from src.platform.security.user import AccountType, User


class UserStore(Protocol):
    """The slice of user persistence that authentication depends on."""

    def get_by_id(self, user_id: str) -> Optional[User]: ...

    def get_by_username(self, username: str) -> Optional[User]: ...

    def get_all(self) -> List[User]: ...

    def exists_by_username(self, username: str) -> bool: ...

    def exists_by_email(self, email: str) -> bool: ...

    def create(
        self,
        username: str,
        email: str,
        password_hash: str,
        account_type: AccountType = ...,
    ) -> User: ...

    def create_claiming_instance(
        self,
        username: str,
        email: str,
        password_hash: str,
    ) -> Tuple[User, bool]:
        """Create a user and, in the SAME transaction, attempt to claim the
        instance as its owner.

        Returns ``(user, became_owner)``. When the single-row instance-claim
        sentinel is written by this call (no prior claim), the user is created
        as ADMIN and ``became_owner`` is True. When a claim already exists, the
        sentinel insert is rejected by its constraint, the user is created as a
        regular USER, and ``became_owner`` is False. Because both writes share
        one transaction, there is never a claimed-but-userless state, and
        SQLite's single-writer serialisation guarantees at most one owner even
        under concurrent registration.
        """
        ...

    def update_last_login(self, user_id: str) -> Optional[User]: ...

    def update_password(self, user_id: str, password_hash: str) -> Optional[User]: ...
