import logging
from typing import TYPE_CHECKING, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from src.features.api_keys.operations import keys as key_operations
from src.features.api_keys.operations.keys import ACTION_MCP_TOOLS_CHANGED, ApiKeyCollaborators
from src.features.api_keys.settings import TOOLS_CEILING_POLICY_KEY
from src.features.mcp.catalog import CatalogEntry, build_catalog
from src.features.mcp.errors import McpToolError
from src.features.mcp.mappers import LEVEL_CEILING, LEVEL_DEFAULT, LEVEL_KEY, Row, listing_to_dict, summary_of
from src.features.mcp.tool_policy import (
    POLICIES,
    POLICY_ALL_ON,
    POLICY_READ_ONLY_ON,
    LevelPolicy,
    ToolState,
    ceiling_state,
    is_reviewed,
    level_allows,
    resolve_state,
)
from src.platform.database.rows import dt_iso, now_utc
from src.platform.security.api_scopes import ApiScope
from src.platform.security.user import User

if TYPE_CHECKING:
    from src.features.mcp.protocol import McpToolCollaborators

logger = logging.getLogger(__name__)

Overrides = Mapping[str, Optional[bool]]


def ceiling_level(c: "McpToolCollaborators") -> LevelPolicy:
    configured = c.settings.get_setting(TOOLS_CEILING_POLICY_KEY, POLICY_ALL_ON)
    policy = configured if configured in POLICIES else POLICY_READ_ONLY_ON
    return LevelPolicy(policy, c.tool_repository.ceiling_overrides())


def default_level(c: "McpToolCollaborators", user_id: str) -> LevelPolicy:
    stored = c.tool_repository.default_policy(user_id)
    policy = stored if stored in POLICIES else POLICY_READ_ONLY_ON
    return LevelPolicy(policy, c.tool_repository.default_overrides(user_id))


def key_rows(
    entries: Iterable[CatalogEntry], ceiling: LevelPolicy, default: LevelPolicy, disabled: Optional[FrozenSet[str]]
) -> List[Row]:
    return [
        (
            entry,
            resolve_state(
                entry.name, entry.writes, entry.unavailable_reason, ceiling, default, disabled,
                spends=entry.spends, destructive=entry.destructive,
            ),
            is_reviewed(default, entry.name),
        )
        for entry in entries
    ]


def states_for_key(c: "McpToolCollaborators", user_id: str, key_id: str) -> List[Row]:
    return key_rows(
        build_catalog(c.tool_registry),
        ceiling_level(c),
        default_level(c, user_id),
        c.tool_repository.key_disabled(key_id),
    )


def default_listing(c: "McpToolCollaborators", user_id: str) -> dict:
    ceiling, default = ceiling_level(c), default_level(c, user_id)
    rows = key_rows(build_catalog(c.tool_registry), ceiling, default, None)
    return listing_to_dict(LEVEL_DEFAULT, None, default.policy, ceiling.policy, rows)


def key_listing(c: "McpToolCollaborators", user_id: str, key_id: str) -> dict:
    ceiling, default = ceiling_level(c), default_level(c, user_id)
    disabled = c.tool_repository.key_disabled(key_id)
    rows = key_rows(build_catalog(c.tool_registry), ceiling, default, disabled)
    return listing_to_dict(LEVEL_KEY, key_id, None, ceiling.policy, rows)


def ceiling_listing(c: "McpToolCollaborators") -> dict:
    ceiling = ceiling_level(c)
    rows = [
        (
            entry,
            ceiling_state(
                entry.name, entry.writes, entry.unavailable_reason, ceiling,
                spends=entry.spends, destructive=entry.destructive,
            ),
            True,
        )
        for entry in build_catalog(c.tool_registry)
    ]
    return listing_to_dict(LEVEL_CEILING, None, ceiling.policy, ceiling.policy, rows)


def counts_for_keys(
    c: "McpToolCollaborators", key_owners: Iterable[Tuple[str, str]]
) -> Dict[str, Tuple[int, int]]:
    owners_by_key = dict(key_owners)
    if not owners_by_key:
        return {}
    entries = build_catalog(c.tool_registry)
    ceiling = ceiling_level(c)
    defaults = c.tool_repository.defaults_for(owners_by_key.values())
    disabled = c.tool_repository.key_disabled_many(owners_by_key)
    counts: Dict[str, Tuple[int, int]] = {}
    for key_id, user_id in owners_by_key.items():
        stored, overrides = defaults[user_id]
        default = LevelPolicy(stored if stored in POLICIES else POLICY_READ_ONLY_ON, overrides)
        summary = summary_of(key_rows(entries, ceiling, default, disabled.get(key_id, frozenset())))
        counts[key_id] = (summary["exposed"], summary["total"])
    return counts


def _entries_by_name(c: "McpToolCollaborators") -> Dict[str, CatalogEntry]:
    return {entry.name: entry for entry in build_catalog(c.tool_registry)}


def _check_policy(policy: Optional[str]) -> None:
    if policy is not None and policy not in POLICIES:
        raise McpToolError("invalid_policy", f"policy must be one of: {', '.join(POLICIES)}")


def _check_names(entries: Mapping[str, CatalogEntry], overrides: Overrides) -> None:
    for name in overrides:
        if name not in entries:
            raise McpToolError("unknown_tool", f"Unknown tool: {name}")


def _check_not_locked(
    entries: Mapping[str, CatalogEntry], overrides: Overrides, ceiling: LevelPolicy
) -> None:
    for name in overrides:
        entry = entries[name]
        if not entry.unavailable_reason and not level_allows(
            ceiling, name, entry.writes, entry.spends, entry.destructive
        ):
            raise McpToolError("locked_by_admin", f"Tool '{name}' is turned off by your administrator", 409)


def _switchable(entries: Mapping[str, CatalogEntry], overrides: Overrides) -> Dict[str, Optional[bool]]:
    return {name: value for name, value in overrides.items() if not entries[name].unavailable_reason}


def patch_default(
    c: "McpToolCollaborators", user_id: str, policy: Optional[str], overrides: Optional[Overrides]
) -> dict:
    wanted = overrides or {}
    if policy is None and not wanted:
        raise McpToolError("nothing_to_change", "Send a policy or overrides.")
    _check_policy(policy)
    entries = _entries_by_name(c)
    _check_names(entries, wanted)
    _check_not_locked(entries, wanted, ceiling_level(c))
    c.tool_repository.apply_default(user_id, policy, _switchable(entries, wanted))
    return default_listing(c, user_id)


def patch_ceiling(
    c: "McpToolCollaborators", admin_id: str, policy: Optional[str], overrides: Optional[Overrides]
) -> dict:
    wanted = overrides or {}
    if policy is None and not wanted:
        raise McpToolError("nothing_to_change", "Send a policy or overrides.")
    _check_policy(policy)
    entries = _entries_by_name(c)
    _check_names(entries, wanted)
    if policy is not None:
        c.settings.set_setting(TOOLS_CEILING_POLICY_KEY, policy)
    c.tool_repository.apply_ceiling(_switchable(entries, wanted), admin_id)
    return ceiling_listing(c)


def key_for_tools(keys: ApiKeyCollaborators, owner: User, key_id: str):
    key = key_operations.owned_key(keys, key_id, owner.id)
    if ApiScope.MCP not in key.scopes:
        raise McpToolError("key_missing_mcp", "This key does not have the mcp scope.")
    return key


def patch_key(
    c: "McpToolCollaborators",
    keys: ApiKeyCollaborators,
    owner: User,
    key_id: str,
    overrides: Optional[Overrides],
    reset: bool,
) -> dict:
    key = key_for_tools(keys, owner, key_id)
    wanted = overrides or {}
    if not reset and not wanted:
        raise McpToolError("nothing_to_change", "Send overrides or reset.")
    if reset and wanted:
        raise McpToolError("invalid_request", "Send either overrides or reset, not both.")
    if any(value is True for value in wanted.values()):
        raise McpToolError("key_can_only_narrow", "A key can only turn tools off. Change your default to turn tools on.")
    entries = _entries_by_name(c)
    _check_names(entries, wanted)
    _check_not_locked(entries, wanted, ceiling_level(c))
    if key.is_revoked:
        raise McpToolError("key_revoked", "A revoked key cannot be changed.", 409)
    before = c.tool_repository.key_disabled(key.id)
    if reset:
        c.tool_repository.reset_key_overrides(key.id)
        after: FrozenSet[str] = frozenset()
    else:
        switchable = _switchable(entries, wanted)
        after = c.tool_repository.apply_key_overrides(
            key.id,
            [name for name, value in switchable.items() if value is False],
            [name for name, value in switchable.items() if value is None],
        )
    if after != before:
        keys.events.add(
            key.id, key.user_id, owner.id, ACTION_MCP_TOOLS_CHANGED,
            {"before": sorted(before), "after": sorted(after)}, dt_iso(now_utc()),
        )
    return key_listing(c, owner.id, key.id)
