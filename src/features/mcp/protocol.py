import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.features.generation.details_url import REQUEST_ORIGIN_KEY
from src.features.llm.tools.base import ToolContext, ToolResult, ToolServices
from src.features.llm.tools.errors import scrub_paths, unexpected
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.mappers import Row
from src.features.mcp.operations.tools import states_for_key
from src.features.mcp.tool_policy import ToolState, refusal_message
from src.features.mcp.tool_repository import McpToolRepository
from src.platform.settings.settings import Settings
from src.platform.version import POTIONUI_VERSION

logger = logging.getLogger(__name__)

SERVER_NAME = "potionui"
SERVER_VERSION = POTIONUI_VERSION
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-06-18", "2025-03-26", "2024-11-05"}

MCP_MODE_ID = "generation"


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


@dataclass(frozen=True)
class McpToolCollaborators:
    """Everything one JSON-RPC request needs to build a `ToolContext` for an
    authenticated MCP token's user. A plain, frozen data holder - built once
    in the composition root (mirrors `ToolContext`'s own field list) and
    passed to the module-level functions below; it has no behavior of its
    own beyond field access, so it is not the "manager" the dissolution
    removed."""
    tool_registry: ToolRegistry
    tool_repository: McpToolRepository
    settings: Settings
    services: ToolServices = field(default_factory=ToolServices)


# --- tool exposure ---

def _execution_llm_id(collaborators: McpToolCollaborators) -> Optional[str]:
    llm_repository = collaborators.services.llm_repository
    default_config = llm_repository.get_default_configuration() if llm_repository else None
    return default_config.id if default_config else None


def _build_tool_context(
    collaborators: McpToolCollaborators, user_id: str, is_admin: bool, llm_id: Optional[str],
    request_origin: str = "", acts_as_admin: bool = False,
) -> ToolContext:
    return ToolContext.for_session(
        collaborators.services,
        user_id,
        mode_id=MCP_MODE_ID,
        is_admin=is_admin,
        acts_as_admin=acts_as_admin,
        session_metadata={REQUEST_ORIGIN_KEY: request_origin},
        chat_session=False,
        llm_id=llm_id,
    )


def _mcp_tool_schema(entry: Any, rendered: Dict[str, Any]) -> Dict[str, Any]:
    function = rendered.get("function", {})
    return {
        "name": function.get("name", entry.name),
        "title": entry.label,
        "description": entry.tool.mcp_description or function.get("description", entry.tool.description),
        "inputSchema": entry.tool.mcp_parameters or function.get("parameters", entry.tool.parameters),
        "annotations": {
            "readOnlyHint": not entry.writes,
            **({"destructiveHint": True} if entry.destructive else {}),
        },
    }


def _on_rows(rows: List[Row]) -> List[Row]:
    return [row for row in rows if row[1] == ToolState.ON]


def list_tools(collaborators: McpToolCollaborators, user_id: str, key_id: str) -> Dict[str, Any]:
    on = _on_rows(states_for_key(collaborators, user_id, key_id))
    if not on:
        return {"tools": []}
    schemas = collaborators.tool_registry.get_schemas(sorted(entry.name for entry, _, _ in on))
    by_name = {s.get("function", {}).get("name"): s for s in schemas}
    return {
        "tools": [
            _mcp_tool_schema(entry, by_name[entry.name])
            for entry, _, _ in on
            if entry.name in by_name
        ]
    }


async def call_tool(
    collaborators: McpToolCollaborators, user_id: str, key_id: str, name: str, arguments: Dict[str, Any],
    is_admin: bool = False, request_origin: str = "", acts_as_admin: bool = False,
) -> Dict[str, Any]:
    row = next((row for row in states_for_key(collaborators, user_id, key_id) if row[0].name == name), None)
    if row is None:
        raise JsonRpcError(INVALID_PARAMS, f"Unknown tool: {name}")
    entry, state, _ = row
    if state != ToolState.ON:
        raise JsonRpcError(INVALID_PARAMS, refusal_message(name, state, entry.unavailable_reason))

    tool = entry.tool
    context = _build_tool_context(
        collaborators, user_id, is_admin, _execution_llm_id(collaborators), request_origin, acts_as_admin
    )
    try:
        if tool.approval_required(arguments):
            # The MCP client already gated this call behind its own
            # consent UI — run the confirmed action directly.
            result = await tool.execute_confirmed(context, **arguments)
        else:
            result = await tool.execute(context, **arguments)
    except Exception as exc:
        logger.error("MCP tool '%s' raised: %s", name, exc, exc_info=True)
        result = ToolResult(success=False, data="", error=unexpected(name, "run", exc, is_admin))

    text = result.data if result.success else f"Error: {scrub_paths(str(result.error or 'the tool failed'))}"
    content: List[Dict[str, Any]] = [{"type": "text", "text": text}]
    if result.success and result.mcp_content:
        content.extend(result.mcp_content)
    return {"content": content, "isError": not result.success}


# --- JSON-RPC dispatch ---

async def handle_method(
    collaborators: McpToolCollaborators, method: str, params: Dict[str, Any], user_id: str,
    *, key_id: str, is_admin: bool = False, request_origin: str = "", acts_as_admin: bool = False,
) -> Optional[Dict[str, Any]]:
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
        return list_tools(collaborators, user_id, key_id)
    if method == "tools/call":
        name = params.get("name")
        if not name:
            raise JsonRpcError(INVALID_PARAMS, "Missing required param: name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "'arguments' must be an object")
        return await call_tool(
            collaborators, user_id, key_id, name, arguments, is_admin=is_admin, request_origin=request_origin,
            acts_as_admin=acts_as_admin,
        )
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
