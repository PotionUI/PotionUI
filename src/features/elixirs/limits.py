import logging
from dataclasses import dataclass, fields
from typing import Any, Callable, Optional

from src.features.elixirs.dto import (
    MAX_BREW_INVOCATIONS,
    MAX_STORED_ASSETS,
    MAX_STORED_EDGES,
    MAX_STORED_NODES,
)

logger = logging.getLogger(__name__)

SETTING_PREFIX = "elixirs_"


@dataclass(frozen=True)
class Limits:
    max_nodes: int = 24
    max_edges: int = 48
    max_branches: int = 8
    max_media_per_source: int = 1000
    max_outputs_per_connection: int = 1000
    max_invocations: int = 200


LIMIT_CAPS = {
    "max_nodes": MAX_STORED_NODES,
    "max_edges": MAX_STORED_EDGES,
    "max_branches": MAX_STORED_EDGES,
    "max_media_per_source": MAX_STORED_ASSETS,
    "max_outputs_per_connection": MAX_STORED_ASSETS,
    "max_invocations": MAX_BREW_INVOCATIONS,
}


def limits_from_settings(read: Callable[[str], Optional[str]]) -> Limits:
    values = {}
    for spec in fields(Limits):
        raw = read(spec.name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            number = int(float(str(raw).strip()))
        except (ValueError, OverflowError):
            logger.warning("Elixirs setting %s=%r is not a number; using %s", spec.name, raw, spec.default)
            continue
        if number < 1:
            logger.warning("Elixirs setting %s=%r must be at least 1; using %s", spec.name, raw, spec.default)
            continue
        cap = LIMIT_CAPS[spec.name]
        if number > cap:
            logger.warning("Elixirs setting %s=%r is above the most allowed; using %s", spec.name, raw, cap)
            number = cap
        values[spec.name] = number
    return Limits(**values)


def stored_limits(setting_repository: Any) -> Callable[[], Limits]:
    def read(name: str) -> Optional[str]:
        setting = setting_repository.get_setting_by_key(f"{SETTING_PREFIX}{name}")
        return None if setting is None else setting.value

    def current() -> Limits:
        return limits_from_settings(read)

    return current
