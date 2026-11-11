import json
from typing import Any, Optional

from src.platform.security.api_scopes import ApiScope

API_KEYS_ENABLED_KEY = "api_keys_enabled"
SCOPES_ALLOWED_KEY = "api_key_scopes_allowed"
MAX_LIFETIME_DAYS_KEY = "api_key_max_lifetime_days"
TOOLS_CEILING_POLICY_KEY = "mcp_tools_ceiling_policy"
MCP_ENABLED_KEY = "mcp_enabled"

DEFAULT_SCOPES_ALLOWED = (ApiScope.READ, ApiScope.GENERATE, ApiScope.CONTENT, ApiScope.MCP)
MAX_LIFETIME_DAYS_LIMIT = 3650
CEILING_POLICIES = ("all_on", "read_only_on")

_OWNED = frozenset({API_KEYS_ENABLED_KEY, SCOPES_ALLOWED_KEY, MAX_LIFETIME_DAYS_KEY, TOOLS_CEILING_POLICY_KEY})


def validate_setting(key: str, value: Any) -> Optional[str]:
    if key not in _OWNED:
        return None
    if key == API_KEYS_ENABLED_KEY:
        return None if isinstance(value, bool) or str(value).lower() in ("true", "false") else "must be true or false"
    if key == SCOPES_ALLOWED_KEY:
        parsed = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return "must be a list of scope names"
        if not isinstance(parsed, list):
            return "must be a list of scope names"
        known = {scope.value for scope in ApiScope}
        unknown = [item for item in parsed if item not in known]
        if unknown:
            return f"unknown scope: {unknown[0]}"
        return None
    if key == MAX_LIFETIME_DAYS_KEY:
        if isinstance(value, bool):
            return f"must be a whole number of days between 0 and {MAX_LIFETIME_DAYS_LIMIT}"
        try:
            days = int(value)
        except (TypeError, ValueError):
            return f"must be a whole number of days between 0 and {MAX_LIFETIME_DAYS_LIMIT}"
        if not 0 <= days <= MAX_LIFETIME_DAYS_LIMIT:
            return f"must be between 0 and {MAX_LIFETIME_DAYS_LIMIT}"
        return None
    if value not in CEILING_POLICIES:
        return f"must be one of: {', '.join(CEILING_POLICIES)}"
    return None
