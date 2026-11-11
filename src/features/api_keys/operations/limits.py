import json
from dataclasses import dataclass
from typing import FrozenSet

from src.features.api_keys.settings import (
    API_KEYS_ENABLED_KEY,
    DEFAULT_SCOPES_ALLOWED,
    MAX_LIFETIME_DAYS_KEY,
    MCP_ENABLED_KEY,
    SCOPES_ALLOWED_KEY,
)
from src.platform.security.api_scopes import ApiScope
from src.platform.settings.settings import Settings


@dataclass(frozen=True)
class InstanceLimits:
    keys_enabled: bool
    mcp_enabled: bool
    scopes_allowed: FrozenSet[ApiScope]
    max_lifetime_days: int


def _allowed_scopes(raw) -> FrozenSet[ApiScope]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not isinstance(raw, list):
        raw = [scope.value for scope in DEFAULT_SCOPES_ALLOWED]
    allowed = set()
    for value in raw:
        try:
            allowed.add(ApiScope(value))
        except ValueError:
            continue
    allowed.add(ApiScope.READ)
    return frozenset(allowed)


def _max_lifetime(raw) -> int:
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(days, 0)


def load_limits(settings: Settings) -> InstanceLimits:
    return InstanceLimits(
        keys_enabled=bool(settings.get_setting(API_KEYS_ENABLED_KEY, True)),
        mcp_enabled=bool(settings.get_setting(MCP_ENABLED_KEY, False)),
        scopes_allowed=_allowed_scopes(settings.get_setting(SCOPES_ALLOWED_KEY, None)),
        max_lifetime_days=_max_lifetime(settings.get_setting(MAX_LIFETIME_DAYS_KEY, 0)),
    )
