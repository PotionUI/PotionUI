"""
MCP token minting/revocation and the global/per-user MCP toggles.

Post-Manager reference shape (see `src.features.plugins.operations`): no
class holds these collaborators together. Each operation is a module-level
function that takes exactly the collaborators it needs (`McpTokenRepository`,
`SettingsManager`, `UserRepository`) as leading arguments, followed by the
operation's own parameters. `McpController` (`routes.py`) holds the
collaborators and passes them in; nothing here is stored across calls.

One concern (MCP token + toggle administration), small enough for a single
module - split it out before it outgrows ~200 lines rather than let a second
concern move in here.

Token hashing uses SHA-256, not the bcrypt `PasswordHasher` the rest of the
app hashes credentials with: a minted token is a 256-bit `secrets.token_urlsafe`
value, not a human-chosen password, so it carries its own entropy and gains
nothing from a deliberately slow KDF - and unlike a login check, every MCP
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


def mint_token(token_repository: McpTokenRepository, user_id: str, name: str) -> Tuple[McpToken, str]:
    """Create a token, returning the row and the plaintext (shown once)."""
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = token_repository.create(
        id=generate_ulid(),
        user_id=user_id,
        name=name,
        token_hash=hash_token(plaintext),
        token_prefix=plaintext[:_DISPLAY_PREFIX_LEN],
    )
    return token, plaintext


def revoke_token(token_repository: McpTokenRepository, user_id: str, token_id: str) -> bool:
    return token_repository.revoke(token_id, user_id)


def is_globally_enabled(settings_manager: SettingsManager) -> bool:
    return bool(settings_manager.get_setting(MCP_ENABLED_KEY, False))


def is_user_enabled(settings_manager: SettingsManager, user_id: str) -> bool:
    return bool(settings_manager.get_setting(MCP_USER_ENABLED_KEY, True, user_id=user_id))


def set_user_enabled(
    settings_manager: SettingsManager, user_repository: UserRepository, user_id: str, enabled: bool
) -> bool:
    """Admin-set per-user MCP toggle. Raises ValueError for an unknown user."""
    if not user_repository.get_by_id(user_id):
        raise ValueError("User not found")
    return settings_manager.set_setting(MCP_USER_ENABLED_KEY, enabled, user_id=user_id)


def resolve_active_token(token_repository: McpTokenRepository, plaintext: str) -> Optional[McpToken]:
    """The non-revoked token matching `plaintext`, or None."""
    token = token_repository.get_by_hash(hash_token(plaintext))
    if token is None or token.is_revoked:
        return None
    return token


def record_use(token_repository: McpTokenRepository, token: McpToken) -> None:
    """Bump `last_used_at`, throttled: skipped when the existing timestamp is
    already fresher than `_LAST_USED_THROTTLE`, so a busy MCP client doesn't
    write this row on every single request."""
    if token.last_used_at:
        try:
            last = datetime.fromisoformat(token.last_used_at)
            if datetime.now(timezone.utc) - last < _LAST_USED_THROTTLE:
                return
        except ValueError:
            pass
    token_repository.touch_last_used(token.id)
