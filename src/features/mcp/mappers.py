from typing import Iterable, List, Optional, Tuple

from src.features.mcp.catalog import CatalogEntry
from src.features.mcp.tool_policy import ToolState

Row = Tuple[CatalogEntry, ToolState, bool]

LEVEL_DEFAULT = "default"
LEVEL_KEY = "key"
LEVEL_CEILING = "ceiling"

_NOT_SWITCHABLE = (ToolState.UNAVAILABLE, ToolState.LOCKED_BY_ADMIN)


def tool_to_dict(entry: CatalogEntry, state: ToolState, reviewed: bool) -> dict:
    return {
        "name": entry.name,
        "label": entry.label,
        "group": entry.group,
        "description": entry.description,
        "writes": entry.writes,
        "unavailable_reason": entry.unavailable_reason,
        "state": state.value,
        "reviewed": reviewed,
    }


def summary_of(rows: Iterable[Row]) -> dict:
    rows = list(rows)
    return {
        "exposed": sum(1 for _, state, _ in rows if state == ToolState.ON),
        "total": sum(1 for _, state, _ in rows if state not in _NOT_SWITCHABLE),
        "writes_exposed": sum(1 for entry, state, _ in rows if state == ToolState.ON and entry.writes),
        "unavailable": sum(1 for _, state, _ in rows if state == ToolState.UNAVAILABLE),
        "locked": sum(1 for _, state, _ in rows if state == ToolState.LOCKED_BY_ADMIN),
        "unreviewed": sum(1 for _, state, reviewed in rows if not reviewed and state not in _NOT_SWITCHABLE),
    }


def listing_to_dict(
    level: str, key_id: Optional[str], policy: Optional[str], ceiling_policy: str, rows: List[Row]
) -> dict:
    listing = {
        "level": level,
        "key_id": key_id,
        "ceiling_policy": ceiling_policy,
        "summary": summary_of(rows),
        "tools": [tool_to_dict(entry, state, reviewed) for entry, state, reviewed in rows],
    }
    if policy is not None:
        listing["policy"] = policy
    return listing
