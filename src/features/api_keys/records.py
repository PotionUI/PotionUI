import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet, List, Optional

from src.platform.database.rows import dt_column
from src.platform.security.api_scopes import ApiScope

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"


def _scopes_from_column(raw: str) -> FrozenSet[ApiScope]:
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return frozenset()
    scopes = set()
    for value in values if isinstance(values, list) else []:
        try:
            scopes.add(ApiScope(value))
        except ValueError:
            continue
    return frozenset(scopes)


@dataclass
class ApiKey:
    id: str
    user_id: str
    name: str
    secret_hash: str
    display_prefix: str
    last_four: Optional[str]
    scopes: FrozenSet[ApiScope]
    created_at: str
    expires_at: Optional[str] = None
    last_used_at: Optional[str] = None
    last_used_via: Optional[str] = None
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        if not self.expires_at:
            return False
        expires = dt_column(self.expires_at)
        if expires is None:
            return True
        return expires <= (now or datetime.now(timezone.utc))

    def status(self, now: Optional[datetime] = None) -> str:
        if self.is_revoked:
            return STATUS_REVOKED
        if self.is_expired(now):
            return STATUS_EXPIRED
        return STATUS_ACTIVE

    @property
    def scope_values(self) -> List[str]:
        return [scope.value for scope in ApiScope if scope in self.scopes]

    @classmethod
    def from_row(cls, row) -> "ApiKey":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            secret_hash=row["secret_hash"],
            display_prefix=row["display_prefix"],
            last_four=row["last_four"],
            scopes=_scopes_from_column(row["scopes"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            last_used_via=row["last_used_via"],
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
        )


@dataclass
class ApiKeyEvent:
    id: str
    key_id: str
    owner_id: str
    actor_id: Optional[str]
    action: str
    detail: dict
    created_at: str

    @classmethod
    def from_row(cls, row) -> "ApiKeyEvent":
        try:
            detail = json.loads(row["detail"]) if row["detail"] else {}
        except ValueError:
            detail = {}
        return cls(
            id=row["id"],
            key_id=row["key_id"],
            owner_id=row["owner_id"],
            actor_id=row["actor_id"],
            action=row["action"],
            detail=detail if isinstance(detail, dict) else {},
            created_at=row["created_at"],
        )


@dataclass
class UserApiAccess:
    user_id: str
    keys_enabled: bool = True
    mcp_enabled: bool = True

    @classmethod
    def from_row(cls, row) -> "UserApiAccess":
        return cls(
            user_id=row["user_id"],
            keys_enabled=bool(row["keys_enabled"]),
            mcp_enabled=bool(row["mcp_enabled"]),
        )
