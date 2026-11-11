import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import FrozenSet, List, Optional, Sequence, Tuple

from src.features.api_keys.errors import ApiKeyError
from src.features.api_keys.operations import secret_format
from src.features.api_keys.operations.expiry import admin_expiry_is_within_cap, expiry_for_new_key, stamp
from src.features.api_keys.operations.limits import InstanceLimits, load_limits
from src.features.api_keys.records import STATUS_ACTIVE, ApiKey, ApiKeyEvent, UserApiAccess
from src.platform.database.rows import dt_column
from src.features.api_keys.repository import ApiKeyEventRepository, ApiKeyRepository, UserApiAccessRepository
from src.platform.security.api_scopes import ApiScope
from src.platform.security.user import AccountType, User
from src.platform.settings.settings import Settings
from src.platform.util.ids import generate_ulid

NAME_MAX_LENGTH = 80

ACTION_CREATED = "created"
ACTION_RENAMED = "renamed"
ACTION_SCOPES_CHANGED = "scopes_changed"
ACTION_REVOKED = "revoked"
ACTION_EXPIRED = "expired"


@dataclass(frozen=True)
class ApiKeyCollaborators:
    keys: ApiKeyRepository
    events: ApiKeyEventRepository
    access: UserApiAccessRepository
    settings: Settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean_name(raw: Optional[str]) -> str:
    name = (raw or "").strip()
    if not name or len(name) > NAME_MAX_LENGTH:
        raise ApiKeyError("invalid_name", f"name must be 1 to {NAME_MAX_LENGTH} characters")
    return name


def parse_scopes(raw: Optional[Sequence[str]]) -> FrozenSet[ApiScope]:
    if not raw:
        raise ApiKeyError("invalid_scopes", "choose at least one scope")
    scopes = set()
    for value in raw:
        try:
            scopes.add(ApiScope(value))
        except ValueError:
            raise ApiKeyError("invalid_scopes", "unknown scope") from None
    return frozenset(scopes)


def effective_switches(c: ApiKeyCollaborators, user_id: str) -> Tuple[InstanceLimits, UserApiAccess]:
    return load_limits(c.settings), c.access.get(user_id)


def _require_keys_enabled(limits: InstanceLimits, access: UserApiAccess) -> None:
    if not limits.keys_enabled or not access.keys_enabled:
        raise ApiKeyError("keys_disabled", "API keys are turned off.", 403)


def _check_grantable(
    owner: User, added: FrozenSet[ApiScope], limits: InstanceLimits, access: UserApiAccess
) -> None:
    for scope in sorted(added, key=lambda item: item.value):
        if scope == ApiScope.ADMIN and owner.account_type != AccountType.ADMIN:
            raise ApiKeyError("scope_not_allowed", "Only an administrator can use the admin scope.", 403)
        if scope not in limits.scopes_allowed:
            raise ApiKeyError("scope_not_allowed", f"The '{scope.value}' scope is not allowed on this instance.", 403)
    if ApiScope.MCP in added and not (limits.mcp_enabled and access.mcp_enabled):
        raise ApiKeyError("mcp_disabled", "MCP is turned off, so a key cannot get the mcp scope.", 403)


def mint(
    c: ApiKeyCollaborators,
    owner: User,
    name: Optional[str],
    scopes: Optional[Sequence[str]],
    expires_in_days: Optional[int],
    expires_provided: bool,
) -> Tuple[ApiKey, str]:
    limits, access = effective_switches(c, owner.id)
    _require_keys_enabled(limits, access)
    clean = clean_name(name)
    granted = parse_scopes(scopes)
    _check_grantable(owner, granted, limits, access)
    now = _now()
    expires_at = expiry_for_new_key(granted, expires_in_days, expires_provided, limits, now)
    secret = secret_format.generate_secret()
    key = c.keys.create(
        ApiKey(
            id=generate_ulid(),
            user_id=owner.id,
            name=clean,
            secret_hash=secret_format.hash_secret(secret),
            display_prefix=secret_format.display_prefix(secret),
            last_four=secret_format.last_four(secret),
            scopes=granted,
            created_at=stamp(now),
            expires_at=expires_at,
        )
    )
    c.events.add(
        key.id, owner.id, owner.id, ACTION_CREATED,
        {"name": key.name, "scopes": key.scope_values, "expires_at": key.expires_at},
        stamp(now),
    )
    return key, secret


def owned_key(c: ApiKeyCollaborators, key_id: str, owner_id: str) -> ApiKey:
    key = c.keys.get_by_id(key_id)
    if key is None or key.user_id != owner_id:
        raise ApiKeyError("key_not_found", "Key not found", 404)
    return key


def update(
    c: ApiKeyCollaborators,
    owner: User,
    key_id: str,
    name: Optional[str],
    scopes: Optional[Sequence[str]],
) -> ApiKey:
    key = owned_key(c, key_id, owner.id)
    if key.is_revoked:
        raise ApiKeyError("key_revoked", "A revoked key cannot be changed.", 409)
    new_name = clean_name(name) if name is not None else key.name
    new_scopes = parse_scopes(scopes) if scopes is not None else key.scopes
    now = _now()
    added = new_scopes - key.scopes
    if added:
        limits, access = effective_switches(c, owner.id)
        _require_keys_enabled(limits, access)
        _check_grantable(owner, added, limits, access)
        if ApiScope.ADMIN in added and not admin_expiry_is_within_cap(key.expires_at, now):
            raise ApiKeyError(
                "admin_scope_needs_expiry",
                "A key that can administer must expire within 90 days. Create a new key instead.",
            )
    if new_name == key.name and new_scopes == key.scopes:
        return key
    renamed, rescoped = new_name != key.name, new_scopes != key.scopes
    updated = replace(key, name=new_name, scopes=new_scopes)
    if not c.keys.update(key.id, new_name, json.dumps(updated.scope_values)):
        raise ApiKeyError("key_revoked", "A revoked key cannot be changed.", 409)
    if renamed:
        c.events.add(key.id, key.user_id, owner.id, ACTION_RENAMED, {"before": key.name, "after": new_name}, stamp(now))
    if rescoped:
        c.events.add(
            key.id, key.user_id, owner.id, ACTION_SCOPES_CHANGED,
            {"before": key.scope_values, "after": updated.scope_values}, stamp(now),
        )
    return updated


def revoke(c: ApiKeyCollaborators, key: ApiKey, actor: User) -> ApiKey:
    now = stamp(_now())
    if c.keys.revoke(key.id, actor.id, now):
        c.events.add(
            key.id, key.user_id, actor.id, ACTION_REVOKED,
            {"by_owner": actor.id == key.user_id}, now,
        )
    return c.keys.get_by_id(key.id) or key


def revoke_own(c: ApiKeyCollaborators, owner: User, key_id: str) -> ApiKey:
    return revoke(c, owned_key(c, key_id, owner.id), owner)


def revoke_any(c: ApiKeyCollaborators, admin: User, key_id: str) -> ApiKey:
    key = c.keys.get_by_id(key_id)
    if key is None:
        raise ApiKeyError("key_not_found", "Key not found", 404)
    return revoke(c, key, admin)


def list_for_user(c: ApiKeyCollaborators, user_id: str) -> List[ApiKey]:
    now = _now()
    return sorted(c.keys.list_for_user(user_id), key=lambda key: key.status(now) != STATUS_ACTIVE)


def key_history(c: ApiKeyCollaborators, key: ApiKey) -> List[ApiKeyEvent]:
    events = c.events.list_for_key(key.id)
    if key.expires_at and key.is_expired():
        ended = key.revoked_at
        if ended is None or (dt_column(key.expires_at) or _now()) <= (dt_column(ended) or _now()):
            events.append(ApiKeyEvent(
                id=f"expired-{key.id}", key_id=key.id, owner_id=key.user_id, actor_id=None,
                action=ACTION_EXPIRED, detail={}, created_at=key.expires_at,
            ))
    return sorted(events, key=lambda event: (dt_column(event.created_at) or _now(), event.id))
