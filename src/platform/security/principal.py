from dataclasses import dataclass
from typing import TYPE_CHECKING, FrozenSet, Optional, Protocol

from src.platform.security.api_scopes import ApiScope

if TYPE_CHECKING:
    from src.platform.security.user import User

KEY_PREFIX = "pui_"


@dataclass(frozen=True)
class KeyGrant:
    key_id: str
    user_id: str
    scopes: FrozenSet[ApiScope]
    display_prefix: str
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class ResolvedKey:
    user: "User"
    grant: KeyGrant


@dataclass(frozen=True)
class Principal:
    user: "User"
    key: Optional[KeyGrant] = None


class KeyResolver(Protocol):
    def resolve(self, plaintext: str) -> Optional[ResolvedKey]: ...

    def mcp_gate(self, grant: KeyGrant) -> Optional[str]: ...

    def record_use(self, grant: KeyGrant, via: str) -> None: ...
