"""LLM chat tool governance: an admin-level enable/lock per (LLM config, tool)
pair plus a global per-user opt-out set, composed on top of the existing
mode/session tool filtering (see
``ChatContextBuilder.resolve_session_prompt_and_tools``).

Governance is per LLM CONFIGURATION, not global - the same tool can be
enabled in one config and disabled in another, since different configs often
serve different audiences or purposes. A session's effective tools are
resolved against whichever config that session actually uses
(``ChatSession.llm_config_id``).

Layering: a config's ``enabled=False`` removes a tool for everyone using that
config, regardless of mode or user preference. A config's ``locked=True``
means a user's own opt-out (if any) is ignored for that config - they cannot
turn the tool off there. A (config, tool) pair with no governance row
defaults to enabled + unlocked, so adding this feature changes no behavior
until an admin acts. The user opt-out set itself is NOT per-config - it is
the user's standing preference, only overridden where a config locks the
tool on.
"""

import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from src.features.llm.tools.governance_repository import ToolGovernanceRepository

logger = logging.getLogger(__name__)


class ToolNotFoundException(Exception):
    """Raised for a tool name that is neither registered nor already governed."""


class ToolLockedException(Exception):
    """Raised when a user tries to change their preference for a tool an admin locked."""


class ToolAdminDisabledException(Exception):
    """Raised when a user tries to change their preference for a tool an admin turned off."""


def compute_allowed_tool_names(
    mode_tool_names: Iterable[str],
    admin_config: Dict[str, Any],
    user_disabled: Iterable[str],
) -> List[str]:
    """The mode's tool names filtered by governance: `enabled=False` in
    `admin_config` (one LLM config's governance rows) always wins; a user's
    opt-out is honored only when the tool isn't `locked` in that same config.
    `admin_config` values may be the repository's {"enabled": ..,
    "locked": ..} dicts or its (enabled, locked) tuple snapshot - both read
    the same way here. A name absent from `admin_config` defaults to enabled
    + unlocked. Config-agnostic (an empty `admin_config` degrades to pure
    passthrough), so the same function serves both the per-config admin
    listing and the config-scoped session resolution.
    """
    disabled_set = set(user_disabled)
    allowed = []
    for name in mode_tool_names:
        row = admin_config.get(name)
        if row is None:
            admin_enabled, locked = True, False
        elif isinstance(row, dict):
            admin_enabled, locked = row.get("enabled", True), row.get("locked", False)
        else:
            admin_enabled, locked = row
        if not admin_enabled:
            continue
        if name in disabled_set and not locked:
            continue
        allowed.append(name)
    return allowed


def build_admin_toolset_listing(tools: List[Any], admin_config: Dict[str, Dict[str, bool]], source_of) -> List[Dict[str, Any]]:
    """Every registered tool merged with its governance row, for the admin
    Toolset tab. `source_of` is `ToolRegistry.source_of`."""
    listing = []
    for tool in tools:
        row = admin_config.get(tool.name, {})
        listing.append({
            "name": tool.name,
            "label": tool.label,
            "group": tool.group,
            "user_description": tool.user_description,
            "requires_approval": tool.requires_approval,
            "enabled": row.get("enabled", True),
            "locked": row.get("locked", False),
            "source": source_of(tool.name) or "builtin",
        })
    listing.sort(key=lambda t: (t["group"], t["label"]))
    return listing


def build_user_toolset_listing(
    tools: List[Any], admin_config: Dict[str, Dict[str, bool]], user_disabled: Set[str]
) -> List[Dict[str, Any]]:
    """The tools a user may see and toggle: admin-disabled tools are omitted
    entirely rather than shown as unavailable."""
    listing = []
    for tool in tools:
        row = admin_config.get(tool.name, {})
        enabled_by_admin = row.get("enabled", True)
        if not enabled_by_admin:
            continue
        listing.append({
            "name": tool.name,
            "label": tool.label,
            "user_description": tool.user_description,
            "enabled_by_admin": enabled_by_admin,
            "locked": row.get("locked", False),
            "disabled_by_user": tool.name in user_disabled,
        })
    listing.sort(key=lambda t: t["label"])
    return listing


class ToolGovernanceManager:
    """Mutations for tool governance. Reads for the admin/user listing routes
    go straight from the routes to the repository + tool registry (see house
    convention: managers keep mutations only)."""

    def __init__(self, repository: ToolGovernanceRepository, tool_registry):
        self._repo = repository
        self._registry = tool_registry

    def _tool_is_known(self, llm_config_id: Optional[str], tool_name: str) -> bool:
        if self._registry.get(tool_name) is not None:
            return True
        # A tool a removed plugin used to own can still be edited if a
        # governance row for it survives (e.g. re-enabling the plugin later
        # should see its prior config).
        if llm_config_id is not None:
            return self._repo.get_config(llm_config_id, tool_name) is not None
        return False

    def set_admin_config(
        self,
        llm_config_id: str,
        tool_name: str,
        enabled: Optional[bool] = None,
        locked: Optional[bool] = None,
    ) -> Dict[str, bool]:
        if not self._tool_is_known(llm_config_id, tool_name):
            raise ToolNotFoundException(tool_name)
        return self._repo.upsert_config(llm_config_id, tool_name, enabled=enabled, locked=locked)

    def set_user_preference(
        self,
        user_id: str,
        tool_name: str,
        disabled: bool,
        llm_config_id: str,
    ) -> None:
        """Toggle the user's global opt-out for a tool. The opt-out itself is
        not scoped to a config, but the toggle always happens in the context
        of one - the caller (the chat composer) always knows its active
        session's config - so that config's `enabled`/`locked` row is always
        enforced: a user can't opt out of a tool their active config locked
        on, and there's no point opting out of one it already turned off.
        """
        if not self._tool_is_known(llm_config_id, tool_name):
            raise ToolNotFoundException(tool_name)
        config = self._repo.get_config(llm_config_id, tool_name) or {"enabled": True, "locked": False}
        if not config["enabled"]:
            raise ToolAdminDisabledException(tool_name)
        if config["locked"]:
            raise ToolLockedException(tool_name)
        self._repo.set_user_disabled(user_id, tool_name, disabled)
