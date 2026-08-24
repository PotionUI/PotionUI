"""Tool registry for managing available LLM tools."""

import logging
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple

from src.features.llm.tools.base import BaseTool
from src.features.llm.tools.tool_conditionals import render_tool_conditionals

logger = logging.getLogger(__name__)


class ToolScope(Protocol):
    """A chat mode, seen through the two attributes tool visibility reads."""

    id: str
    tool_names: Optional[List[str]]


class ToolRegistry:
    """Registry for LLM tools.

    Manages registration and retrieval of tools, and provides schemas for API
    calls. Tools are scoped to chat modes: a tool is visible in a mode when it
    is global (``modes is None``), declares the mode in ``modes``, or is named
    in the mode's ``tool_names``. System-prompt assembly lives with the chat
    modes (ChatModeRegistry.resolve_system_prompt), not here.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._sources: Dict[str, str] = {}
        # Schemas are pure functions of the registered tool set; a tool-loop
        # iteration calls get_schemas() with the same (session-scoped) name
        # filter repeatedly, so cache the built list and drop it whenever the
        # registry actually changes instead of on a TTL.
        self._schema_cache: Dict[Optional[Tuple[str, ...]], List[Dict]] = {}

    def register(self, tool: BaseTool, source: str = "builtin") -> None:
        """Register a tool in the registry, tracking its source for cleanup."""
        if tool.name in self._tools:
            logger.warning(f"Tool '{tool.name}' already registered, replacing")
        self._tools[tool.name] = tool
        self._sources[tool.name] = source
        self._schema_cache.clear()
        logger.debug(f"Registered tool: {tool.name} (source: {source})")

    def unregister(self, name: str) -> bool:
        """Unregister a tool by name. Returns True if the tool was found and removed."""
        if name in self._tools:
            del self._tools[name]
            self._sources.pop(name, None)
            self._schema_cache.clear()
            logger.debug(f"Unregistered tool: {name}")
            return True
        return False

    def unregister_source(self, source: str) -> int:
        """Unregister all tools registered by the given source (plugin id).

        Returns the number of tools removed.
        """
        to_remove = [name for name, src in self._sources.items() if src == source]
        for name in to_remove:
            del self._tools[name]
            del self._sources[name]
        if to_remove:
            self._schema_cache.clear()
            logger.info(f"Unregistered tools {to_remove} for source '{source}'")
        return len(to_remove)

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def source_of(self, name: str) -> Optional[str]:
        """The registering source ('builtin' or a plugin id) for a tool name."""
        return self._sources.get(name)

    def get_all(self) -> List[BaseTool]:
        """Get all registered tools."""
        return list(self._tools.values())

    def get_for_mode(self, mode: ToolScope) -> List[BaseTool]:
        """Get the tools visible in the given mode.

        Union of: global tools (modes is None), tools declaring the mode id in
        their ``modes`` list, and tools named in ``mode.tool_names``.
        """
        included = set(mode.tool_names or [])
        return [
            tool for tool in self._tools.values()
            if tool.modes is None
            or mode.id in tool.modes
            or tool.name in included
        ]

    def get_schemas(self, names: Optional[List[str]] = None) -> List[Dict]:
        """Get tool schemas for API calls, optionally filtered by name.

        Each schema's descriptions have their ``{{#if}}`` / ``{{#ifany}}``
        tool-conditional blocks resolved against the same name filter, so a tool
        never advertises a cross-reference to a sibling that isn't in this
        session's set (e.g. run_generation's "call get_form_state first" drops
        when get_form_state is disabled). When ``names is None`` the references
        are resolved against every registered tool.

        Cached per distinct name-filter (a tool loop calls this with the same
        filter on every iteration); the cache is cleared on any registration
        change, so it never serves a stale schema list.
        """
        cache_key = tuple(sorted(names)) if names is not None else None
        cached = self._schema_cache.get(cache_key)
        if cached is not None:
            return cached
        allowed: Optional[Set[str]] = set(names) if names is not None else set(self._tools.keys())
        tools = self._tools.values()
        if names is not None:
            tools = [t for t in tools if t.name in names]
        schemas = [self._render_schema_conditionals(tool.to_schema(), allowed) for tool in tools]
        self._schema_cache[cache_key] = schemas
        return schemas

    @classmethod
    def _render_schema_conditionals(cls, node: Any, allowed: Optional[Set[str]]) -> Any:
        """Resolve tool-conditional markers in every ``description`` string of a schema."""
        if isinstance(node, dict):
            return {
                key: (
                    render_tool_conditionals(value, allowed)
                    if key == "description" and isinstance(value, str)
                    else cls._render_schema_conditionals(value, allowed)
                )
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [cls._render_schema_conditionals(item, allowed) for item in node]
        return node

    def get_tool_hints_text(self, names: Optional[List[str]] = None) -> str:
        """Get the formatted tool hints text, optionally filtered by name.

        Each hint's tool-conditional blocks are resolved against the filter, so a
        hint's cross-reference to a sibling tool drops when that sibling isn't in
        the session's set.
        """
        allowed: Optional[Set[str]] = set(names) if names is not None else set(self._tools.keys())
        tools = self._tools.values()
        if names is not None:
            tools = [t for t in tools if t.name in names]
        tool_hints = []
        for tool in tools:
            if tool.hint:
                tool_hints.append(f"- {tool.name}: {render_tool_conditionals(tool.hint, allowed)}")
        return "\n".join(tool_hints)
