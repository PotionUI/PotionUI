from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Mapping, Optional

POLICY_ALL_ON = "all_on"
POLICY_READ_ONLY_ON = "read_only_on"
POLICIES = (POLICY_ALL_ON, POLICY_READ_ONLY_ON)


class ToolState(str, Enum):
    ON = "on"
    OFF = "off"
    OFF_IN_DEFAULT = "off_in_default"
    LOCKED_BY_ADMIN = "locked_by_admin"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class LevelPolicy:
    policy: str
    overrides: Mapping[str, bool] = field(default_factory=dict)


def allows(policy: str, writes: bool, spends: bool = False, destructive: bool = False) -> bool:
    if destructive:
        return False
    return policy == POLICY_ALL_ON or not (writes or spends)


def level_allows(level: LevelPolicy, name: str, writes: bool, spends: bool = False, destructive: bool = False) -> bool:
    return level.overrides.get(name, allows(level.policy, writes, spends, destructive))


def is_reviewed(default: LevelPolicy, name: str) -> bool:
    return name in default.overrides


def resolve_state(
    name: str,
    writes: bool,
    unavailable_reason: Optional[str],
    ceiling: LevelPolicy,
    default: LevelPolicy,
    key_disabled: Optional[FrozenSet[str]] = None,
    spends: bool = False,
    destructive: bool = False,
) -> ToolState:
    if unavailable_reason:
        return ToolState.UNAVAILABLE
    if not level_allows(ceiling, name, writes, spends, destructive):
        return ToolState.LOCKED_BY_ADMIN
    user_on = level_allows(default, name, writes, spends, destructive)
    if key_disabled is None:
        return ToolState.ON if user_on else ToolState.OFF
    if not user_on:
        return ToolState.OFF_IN_DEFAULT
    return ToolState.OFF if name in key_disabled else ToolState.ON


def ceiling_state(
    name: str,
    writes: bool,
    unavailable_reason: Optional[str],
    ceiling: LevelPolicy,
    spends: bool = False,
    destructive: bool = False,
) -> ToolState:
    if unavailable_reason:
        return ToolState.UNAVAILABLE
    return ToolState.ON if level_allows(ceiling, name, writes, spends, destructive) else ToolState.OFF


def refusal_message(name: str, state: ToolState, unavailable_reason: Optional[str]) -> str:
    if state == ToolState.UNAVAILABLE:
        return f"Tool '{name}' can't run over MCP: {unavailable_reason}"
    if state == ToolState.LOCKED_BY_ADMIN:
        return f"Tool '{name}' is turned off by your administrator"
    return f"Tool '{name}' is turned off for this key"
