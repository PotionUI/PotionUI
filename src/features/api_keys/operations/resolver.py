import secrets
from typing import Dict, Optional

from src.features.api_keys.operations import secret_format
from src.features.api_keys.operations.limits import load_limits
from src.features.api_keys.repository import ApiKeyRepository, UserApiAccessRepository
from src.platform.database.rows import dt_iso, now_utc
from src.platform.security.principal import KeyGrant, ResolvedKey
from src.platform.settings.settings import Settings

_MEMO_LIMIT = 4096


class ApiKeyResolver:
    def __init__(self, keys: ApiKeyRepository, access: UserApiAccessRepository, users, settings: Settings):
        self._keys = keys
        self._access = access
        self._users = users
        self._settings = settings
        self._recorded_hour: Dict[str, str] = {}

    def resolve(self, plaintext: str) -> Optional[ResolvedKey]:
        if not plaintext or len(plaintext) > secret_format.MAX_PRESENTED_LENGTH or not plaintext.isascii():
            return None
        presented_hash = secret_format.hash_secret(plaintext)
        key = self._keys.get_by_hash(presented_hash)
        if key is None or not secrets.compare_digest(key.secret_hash, presented_hash):
            return None
        if key.is_revoked or key.is_expired():
            return None
        limits = load_limits(self._settings)
        if not limits.keys_enabled or not self._access.get(key.user_id).keys_enabled:
            return None
        owner = self._users.get_by_id(key.user_id)
        if owner is None:
            return None
        scopes = frozenset(scope for scope in key.scopes if scope in limits.scopes_allowed)
        return ResolvedKey(
            user=owner,
            grant=KeyGrant(
                key_id=key.id,
                user_id=key.user_id,
                scopes=scopes,
                display_prefix=key.display_prefix,
                expires_at=key.expires_at,
            ),
        )

    def mcp_gate(self, grant: KeyGrant) -> Optional[str]:
        limits = load_limits(self._settings)
        if not limits.mcp_enabled:
            return "MCP is disabled on this instance"
        if not self._access.get(grant.user_id).mcp_enabled:
            return "MCP is disabled for this user"
        return None

    def record_use(self, grant: KeyGrant, via: str) -> None:
        hour = dt_iso(now_utc().replace(minute=0, second=0, microsecond=0))
        if self._recorded_hour.get(grant.key_id) == hour:
            return
        self._keys.touch_last_used(grant.key_id, hour, via)
        if len(self._recorded_hour) >= _MEMO_LIMIT:
            self._recorded_hour.clear()
        self._recorded_hour[grant.key_id] = hour
