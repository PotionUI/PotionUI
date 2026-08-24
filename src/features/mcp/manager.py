"""Mutations for MCP tokens and the admin per-user MCP toggle.

Token hashing uses SHA-256, not the bcrypt `PasswordHasher` the rest of the
app hashes credentials with: a minted token is a 256-bit `secrets.token_urlsafe`
value, not a human-chosen password, so it carries its own entropy and gains
nothing from a deliberately slow KDF — and unlike a login check, every MCP
request pays this cost, where bcrypt's ~12-round work factor would be pure
added latency for no security benefit against a value nobody can feasibly
guess or brute-force offline in the first place.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from src.features.mcp.records import McpToken
from src.features.mcp.repository import McpTokenRepository
from src.features.users.repository import UserRepository
from src.platform.settings.settings import SettingsManager
from src.platform.util.ids import generate_ulid

TOKEN_PREFIX = "pui_mcp_"
_DISPLAY_PREFIX_LEN = 12
_LAST_USED_THROTTLE = timedelta(seconds=60)

MCP_ENABLED_KEY = "mcp_enabled"
MCP_USER_ENABLED_KEY = "mcp_user_enabled"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class McpManager:
    def __init__(
        self,
        token_repository: McpTokenRepository,
        settings_manager: SettingsManager,
        user_repository: UserRepository,
    ):
        self._tokens = token_repository
        self._settings = settings_manager
        self._users = user_repository

    def mint_token(self, user_id: str, name: str) -> Tuple[McpToken, str]:
        """Create a token, returning the row and the plaintext (shown once)."""
        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
        token = self._tokens.create(
            id=generate_ulid(),
            user_id=user_id,
            name=name,
            token_hash=hash_token(plaintext),
            token_prefix=plaintext[:_DISPLAY_PREFIX_LEN],
        )
        return token, plaintext

    def revoke_token(self, user_id: str, token_id: str) -> bool:
        return self._tokens.revoke(token_id, user_id)

    def is_globally_enabled(self) -> bool:
        return bool(self._settings.get_setting(MCP_ENABLED_KEY, False))

    def is_user_enabled(self, user_id: str) -> bool:
        return bool(self._settings.get_setting(MCP_USER_ENABLED_KEY, True, user_id=user_id))

    def set_user_enabled(self, user_id: str, enabled: bool) -> bool:
        """Admin-set per-user MCP toggle. Raises ValueError for an unknown user."""
        if not self._users.get_by_id(user_id):
            raise ValueError("User not found")
        return self._settings.set_setting(MCP_USER_ENABLED_KEY, enabled, user_id=user_id)

    def resolve_active_token(self, plaintext: str) -> Optional[McpToken]:
        """The non-revoked token matching `plaintext`, or None."""
        token = self._tokens.get_by_hash(hash_token(plaintext))
        if token is None or token.is_revoked:
            return None
        return token

    def record_use(self, token: McpToken) -> None:
        """Bump `last_used_at`, throttled: skipped when the existing
        timestamp is already fresher than `_LAST_USED_THROTTLE`, so a busy
        MCP client doesn't write this row on every single request."""
        if token.last_used_at:
            try:
                last = datetime.fromisoformat(token.last_used_at)
                if datetime.now(timezone.utc) - last < _LAST_USED_THROTTLE:
                    return
            except ValueError:
                pass
        self._tokens.touch_last_used(token.id)
