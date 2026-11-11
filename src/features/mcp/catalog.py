from dataclasses import dataclass
from typing import Any, List, Optional

from src.features.llm.tools.registry import ToolRegistry


@dataclass(frozen=True)
class CatalogEntry:
    tool: Any
    name: str
    label: str
    group: str
    description: str
    writes: bool
    unavailable_reason: Optional[str]


def _entry(tool: Any) -> CatalogEntry:
    return CatalogEntry(
        tool=tool,
        name=tool.name,
        label=tool.label,
        group=tool.group,
        description=tool.user_description or tool.description,
        writes=bool(tool.writes),
        unavailable_reason=tool.mcp_unavailable or None,
    )


def build_catalog(registry: ToolRegistry) -> List[CatalogEntry]:
    return [
        _entry(tool)
        for tool in registry.get_all()
        if tool.mcp_unavailable or (tool.mcp and tool.is_available(None))
    ]
