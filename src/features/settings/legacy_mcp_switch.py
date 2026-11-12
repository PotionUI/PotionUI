from datetime import datetime
from typing import Any, Optional

from src.features.api_keys.records import UserApiAccess
from src.features.api_keys.repository import UserApiAccessRepository
from src.platform.database.rows import now_iso, now_utc

KEY = "mcp_user_enabled"
DESCRIPTION = "Whether this user's MCP tokens are allowed to authenticate. Admin-controlled per user."
VALUE_TYPE = "boolean"
SETTING_TYPE = "USER"

_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def parse(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return None


class LegacyMcpUserSwitch:
    def __init__(self, access: UserApiAccessRepository):
        self._access = access

    def read(self, user_id: str) -> bool:
        return self._access.get(user_id).mcp_enabled

    def write(self, user_id: str, enabled: bool) -> None:
        current = self._access.get(user_id)
        updated = UserApiAccess(user_id=user_id, keys_enabled=current.keys_enabled, mcp_enabled=enabled)
        self._access.save(updated, now_iso(), user_id)

    def detail(self, user_id: str) -> dict:
        now: datetime = now_utc()
        return {
            "id": KEY,
            "key": KEY,
            "value": self.read(user_id),
            "value_type": VALUE_TYPE,
            "description": DESCRIPTION,
            "type": SETTING_TYPE,
            "created_at": now,
            "updated_at": now,
        }

    def override(self, user_id: str) -> Optional[dict]:
        if self.read(user_id):
            return None
        now: datetime = now_utc()
        return {
            "id": f"{KEY}:{user_id}",
            "user_id": user_id,
            "setting_id": KEY,
            "setting_key": KEY,
            "value": False,
            "created_at": now,
            "updated_at": now,
        }
