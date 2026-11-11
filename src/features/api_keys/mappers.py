from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from src.features.api_keys.operations.limits import InstanceLimits
from src.features.api_keys.records import ApiKey, ApiKeyEvent, UserApiAccess
from src.platform.database.rows import dt_iso
from src.platform.security.api_scopes import ApiScope
from src.platform.security.user import AccountType


def api_time(value: Optional[str]) -> Optional[str]:
    return dt_iso(value) or value


def _person(user_id: Optional[str], names: Dict[str, str]) -> Optional[dict]:
    if not user_id:
        return None
    return {"id": user_id, "username": names.get(user_id)}


def key_to_dict(
    key: ApiKey, now: datetime, names: Dict[str, str], tool_counts: Optional[Tuple[int, int]] = None
) -> dict:
    revoked_by = key.revoked_by if key.revoked_by and key.revoked_by != key.user_id else None
    return {
        "id": key.id,
        "name": key.name,
        "display_prefix": key.display_prefix,
        "last_four": key.last_four,
        "scopes": key.scope_values,
        "status": key.status(now),
        "created_at": api_time(key.created_at),
        "expires_at": api_time(key.expires_at),
        "last_used_at": api_time(key.last_used_at),
        "last_used_via": key.last_used_via,
        "revoked_at": api_time(key.revoked_at),
        "revoked_by": _person(revoked_by, names),
        "mcp_tools": (
            {"exposed": tool_counts[0], "total": tool_counts[1]}
            if tool_counts is not None and ApiScope.MCP in key.scopes
            else None
        ),
    }


def related_user_ids(keys: Iterable[ApiKey]) -> List[str]:
    return [key.revoked_by for key in keys if key.revoked_by and key.revoked_by != key.user_id]


def limits_to_dict(limits: InstanceLimits, access: UserApiAccess, owner_type: AccountType) -> dict:
    allowed = [
        scope.value
        for scope in ApiScope
        if scope in limits.scopes_allowed and (scope != ApiScope.ADMIN or owner_type == AccountType.ADMIN)
    ]
    return {
        "keys_enabled": limits.keys_enabled and access.keys_enabled,
        "mcp_enabled": limits.mcp_enabled and access.mcp_enabled,
        "scopes_allowed": allowed,
        "max_lifetime_days": limits.max_lifetime_days or None,
    }


def _changes(event: ApiKeyEvent) -> Optional[dict]:
    detail = event.detail
    if event.action == "renamed":
        return {"name": {"before": detail.get("before"), "after": detail.get("after")}}
    if event.action == "scopes_changed":
        before, after = detail.get("before") or [], detail.get("after") or []
        return {"scopes": {
            "added": [scope for scope in after if scope not in before],
            "removed": [scope for scope in before if scope not in after],
        }}
    if event.action == "mcp_tools_changed":
        before, after = detail.get("before") or [], detail.get("after") or []
        return {"mcp_tools": {
            "turned_off": [name for name in after if name not in before],
            "turned_on": [name for name in before if name not in after],
        }}
    return None


def event_to_dict(event: ApiKeyEvent, names: Dict[str, str]) -> dict:
    return {
        "action": event.action,
        "actor": _person(event.actor_id, names),
        "detail": event.detail,
        "changes": _changes(event),
        "created_at": api_time(event.created_at),
    }


def access_to_dict(access: UserApiAccess) -> dict:
    return {"keys_enabled": access.keys_enabled, "mcp_enabled": access.mcp_enabled}
