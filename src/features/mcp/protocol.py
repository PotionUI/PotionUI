"""Stateless MCP (Model Context Protocol) Streamable-HTTP surface: JSON-RPC 2.0
over plain `application/json` responses, no session persistence.

Exposes a subset of the existing LLM `ToolRegistry` (see
src.features.llm.tools.registry) as MCP tools, gated through the same
governance rules chat sessions apply (src.features.llm.tools.governance) plus
a fixed exclusion list for builtins whose only effect is reading/writing a
live frontend session's `form_state` (Generate-form fields, prompt segments,
Video Director document, Music Director document) — context an MCP caller
has no way to supply. A tool
excluded here always fails with "No form state available" regardless of who
calls it; see `EXCLUDED_TOOL_NAMES` for the full list and why each is there.

`tools/call` runs `requires_approval` tools via `execute_confirmed` directly
rather than the chat path's preview-then-approve two-step: an MCP client
already gates each tool call behind its own user consent UI before the
request ever reaches this endpoint, so there is no second round trip here to
collect approval for.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.features.llm.tools.base import ToolContext, ToolResult
from src.features.llm.tools.governance import compute_allowed_tool_names
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.platform.version import POTIONUI_VERSION

logger = logging.getLogger(__name__)

SERVER_NAME = "potionui"
SERVER_VERSION = POTIONUI_VERSION
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

# Builtins whose result is only meaningful with a live frontend chat session:
# they read `context.session_metadata['form_state'/'segments']`, populated by
# the frontend at chat-send time from the DOM (the active Generate form,
# prompt editor segments, or Video/Music Director document) — an MCP caller
# has no analogous state to hand over, so these always fail. set_prompt_relay_timeline
# is different in kind: it needs no form_state, but its `execute_confirmed`
# just hands back an `{"action": "set_prompt_relay", ...}` payload for the
# CHAT FRONTEND to apply to the live video editor — there is no server-side
# effect at all, so calling it over MCP would silently do nothing.
EXCLUDED_TOOL_NAMES = frozenset({
    "get_active_models",
    "get_current_segments",
    "get_form_state",
    "update_form_settings",
    "run_generation",
    "get_video_director",
    "get_music_director",
    "update_music_director",
    "set_prompt_relay_timeline",
})


@dataclass(frozen=True)
class _McpToolScope:
    """A `ToolScope` (see ToolRegistry.get_for_mode) standing in for the chat
    mode MCP has no equivalent of: MCP exposes exactly the tool surface a
    `generation`-mode chat session sees (every global tool plus every tool
    declaring `generation`), before the exclusion list above narrows it."""
    id: str = "generation"
    tool_names: Optional[List[str]] = None


_MCP_SCOPE = _McpToolScope()


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpProtocolManager:
    """Handles one JSON-RPC request for one authenticated MCP token's user."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_governance_repository: ToolGovernanceRepository,
        llm_repository: Any,
        segment_manager: Any = None,
        model_index_manager: Any = None,
        preset_manager: Any = None,
        phrasebook_manager: Any = None,
        prompt_database_manager: Any = None,
        generation_orchestrator: Any = None,
        llm_memory_manager: Any = None,
        prompt_enhancement_manager: Any = None,
        media_index_manager: Any = None,
        settings_manager: Any = None,
        collection_repository: Any = None,
        tag_repository: Any = None,
        plugin_registry: Any = None,
        generation_history_manager: Any = None,
    ):
        self._registry = tool_registry
        self._governance_repo = tool_governance_repository
        self._llm_repository = llm_repository
        self._segment_manager = segment_manager
        self._model_index_manager = model_index_manager
        self._preset_manager = preset_manager
        self._phrasebook_manager = phrasebook_manager
        self._prompt_database_manager = prompt_database_manager
        self._generation_orchestrator = generation_orchestrator
        self._llm_memory_manager = llm_memory_manager
        self._prompt_enhancement_manager = prompt_enhancement_manager
        self._media_index_manager = media_index_manager
        self._settings_manager = settings_manager
        self._collection_repository = collection_repository
        self._tag_repository = tag_repository
        self._plugin_registry = plugin_registry
        self._generation_history_manager = generation_history_manager

    # --- tool exposure ---

    def _candidate_tools(self) -> List[Any]:
        """Every tool eligible for MCP exposure, before governance."""
        return [
            tool for tool in self._registry.get_for_mode(_MCP_SCOPE)
            if tool.name not in EXCLUDED_TOOL_NAMES and tool.is_available(None)
        ]

    def _allowed_tool_names(self, user_id: str) -> List[str]:
        """Candidate tools narrowed by the same admin/user governance rules a
        chat session's default LLM config applies (see
        ChatContextBuilder.resolve_session_prompt_and_tools)."""
        candidate_names = [t.name for t in self._candidate_tools()]
        default_config = self._llm_repository.get_default_configuration() if self._llm_repository else None
        llm_config_id = default_config.id if default_config else None
        snapshot = (
            self._governance_repo.get_config_snapshot(llm_config_id, candidate_names)
            if llm_config_id else {}
        )
        user_disabled = self._governance_repo.get_user_disabled(user_id)
        return compute_allowed_tool_names(candidate_names, snapshot, user_disabled)

    def _build_tool_context(self, user_id: str) -> ToolContext:
        default_config = self._llm_repository.get_default_configuration() if self._llm_repository else None
        return ToolContext(
            user_id=user_id,
            mode_id=_MCP_SCOPE.id,
            session_metadata={},
            segment_manager=self._segment_manager,
            model_index_manager=self._model_index_manager,
            preset_manager=self._preset_manager,
            phrasebook_manager=self._phrasebook_manager,
            llm_repository=self._llm_repository,
            prompt_database_manager=self._prompt_database_manager,
            generation_orchestrator=self._generation_orchestrator,
            llm_memory_manager=self._llm_memory_manager,
            prompt_enhancement_manager=self._prompt_enhancement_manager,
            media_index_manager=self._media_index_manager,
            settings_manager=self._settings_manager,
            collection_repository=self._collection_repository,
            tag_repository=self._tag_repository,
            plugin_registry=self._plugin_registry,
            generation_history_manager=self._generation_history_manager,
            llm_id=default_config.id if default_config else None,
        )

    @staticmethod
    def _mcp_tool_schema(tool: Any, rendered: Dict[str, Any]) -> Dict[str, Any]:
        """An OpenAI-shaped `tool.to_schema()` (already tool-conditional
        resolved by `ToolRegistry.get_schemas`), reshaped into MCP's
        `{name, description, inputSchema}`."""
        function = rendered.get("function", {})
        return {
            "name": function.get("name", tool.name),
            "description": function.get("description", tool.description),
            "inputSchema": function.get("parameters", tool.parameters),
        }

    def list_tools(self, user_id: str) -> Dict[str, Any]:
        allowed = set(self._allowed_tool_names(user_id))
        if not allowed:
            return {"tools": []}
        schemas = self._registry.get_schemas(sorted(allowed))
        by_name = {s.get("function", {}).get("name"): s for s in schemas}
        tools = [
            self._mcp_tool_schema(tool, by_name[tool.name])
            for tool in self._candidate_tools()
            if tool.name in allowed and tool.name in by_name
        ]
        return {"tools": tools}

    async def call_tool(self, user_id: str, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        allowed = self._allowed_tool_names(user_id)
        if name not in allowed:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name}")

        tool = self._registry.get(name)
        if tool is None:
            raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name}")

        context = self._build_tool_context(user_id)
        try:
            if tool.requires_approval:
                # The MCP client already gated this call behind its own
                # consent UI — run the confirmed action directly.
                result = await tool.execute_confirmed(context, **arguments)
            else:
                result = await tool.execute(context, **arguments)
        except Exception as exc:
            logger.error("MCP tool '%s' raised: %s", name, exc, exc_info=True)
            result = ToolResult(success=False, data="", error=f"Tool execution failed: {exc}")

        text = result.data if result.success else f"Error: {result.error}"
        return {
            "content": [{"type": "text", "text": text}],
            "isError": not result.success,
        }

    # --- JSON-RPC dispatch ---

    async def handle_method(self, method: str, params: Dict[str, Any], user_id: str) -> Optional[Dict[str, Any]]:
        """Returns the JSON-RPC `result` payload, or None for a notification
        that has no response body."""
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {}
        if method == "tools/list":
            return self.list_tools(user_id)
        if method == "tools/call":
            name = params.get("name")
            if not name:
                raise JsonRpcError(INVALID_PARAMS, "Missing required param: name")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise JsonRpcError(INVALID_PARAMS, "'arguments' must be an object")
            return await self.call_tool(user_id, name, arguments)
        raise JsonRpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


def parse_jsonrpc_request(body: Any) -> "tuple[Optional[Any], str, Dict[str, Any]]":
    """Validate the JSON-RPC envelope. Returns (id, method, params).

    `id` is `None` both for an absent id (a notification) and an explicit
    JSON `null` id — callers use the request's own key presence, not this
    return value, to tell notifications apart from id-less error cases.
    """
    if not isinstance(body, dict):
        raise JsonRpcError(INVALID_REQUEST, "Request body must be a JSON object")
    if body.get("jsonrpc") != "2.0":
        raise JsonRpcError(INVALID_REQUEST, "Missing or invalid 'jsonrpc' version")
    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcError(INVALID_REQUEST, "Missing required field: method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise JsonRpcError(INVALID_PARAMS, "'params' must be an object")
    return body.get("id"), method, params
