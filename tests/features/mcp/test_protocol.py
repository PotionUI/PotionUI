"""McpProtocolManager: JSON-RPC dispatch, tool exposure (governance +
exclusion list), and the approval-gated -> execute_confirmed shortcut.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.protocol import (
    EXCLUDED_TOOL_NAMES,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JsonRpcError,
    McpProtocolManager,
    parse_jsonrpc_request,
)


class _EchoTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "echo"

    @property
    def description(self):
        return "Echoes the given text back."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data=kwargs.get("text", ""))


class _FailingTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "boom"

    @property
    def description(self):
        return "Always fails."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        raise RuntimeError("kaboom")


class _ApprovalTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "approve_me"

    @property
    def description(self):
        return "Needs approval before it actually does anything."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def requires_approval(self):
        return True

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="preview-only")

    async def execute_confirmed(self, context, **kwargs):
        return ToolResult(success=True, data="applied!")


class _StubExcludedTool(BaseTool):
    """Stands in for a real builtin on EXCLUDED_TOOL_NAMES (e.g.
    get_form_state) so the exclusion is tested by name, independent of the
    real tool's implementation."""
    modes = ["generation"]

    @property
    def name(self):
        return "get_form_state"

    @property
    def description(self):
        return "stub"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="should never be reachable via MCP")


def _no_default_config():
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    return llm_repository


@pytest.fixture
def protocol(mcp_db):
    registry = ToolRegistry()
    registry.register(_EchoTool())
    registry.register(_FailingTool())
    registry.register(_ApprovalTool())
    registry.register(_StubExcludedTool())
    governance_repo = ToolGovernanceRepository()
    manager = McpProtocolManager(
        tool_registry=registry,
        tool_governance_repository=governance_repo,
        llm_repository=_no_default_config(),
    )
    return manager, registry, governance_repo


class TestParseJsonRpcRequest:
    def test_valid_request(self):
        req_id, method, params = parse_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        )
        assert (req_id, method, params) == (1, "ping", {})

    def test_missing_jsonrpc_version_is_invalid_request(self):
        with pytest.raises(JsonRpcError) as exc:
            parse_jsonrpc_request({"id": 1, "method": "ping"})
        assert exc.value.code == INVALID_REQUEST

    def test_missing_method_is_invalid_request(self):
        with pytest.raises(JsonRpcError) as exc:
            parse_jsonrpc_request({"jsonrpc": "2.0", "id": 1})
        assert exc.value.code == INVALID_REQUEST

    def test_non_object_params_is_invalid_params(self):
        with pytest.raises(JsonRpcError) as exc:
            parse_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": "nope"})
        assert exc.value.code == INVALID_PARAMS

    def test_non_object_body_is_invalid_request(self):
        with pytest.raises(JsonRpcError):
            parse_jsonrpc_request(["not", "an", "object"])

    def test_no_id_is_a_notification_and_still_parses(self):
        req_id, method, _params = parse_jsonrpc_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert req_id is None
        assert method == "notifications/initialized"


class TestHandleMethod:
    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities_and_server_info(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("initialize", {"protocolVersion": "2025-06-18"}, "user-1")
        assert result["protocolVersion"] == "2025-06-18"
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"]["name"] == "potionui"

    @pytest.mark.asyncio
    async def test_initialize_falls_back_to_default_version_for_an_unknown_request(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("initialize", {"protocolVersion": "1999-01-01"}, "user-1")
        assert result["protocolVersion"] == "2025-06-18"

    @pytest.mark.asyncio
    async def test_notifications_initialized_returns_none(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("notifications/initialized", {}, "user-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_returns_empty_object(self, protocol):
        manager, _registry, _gov = protocol
        assert await manager.handle_method("ping", {}, "user-1") == {}

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self, protocol):
        manager, _registry, _gov = protocol
        with pytest.raises(JsonRpcError) as exc:
            await manager.handle_method("nonexistent/method", {}, "user-1")
        assert exc.value.code == METHOD_NOT_FOUND


class TestListTools:
    @pytest.mark.asyncio
    async def test_normal_tools_are_listed_with_mcp_shaped_schema(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert "echo" in names
        echo = next(t for t in result["tools"] if t["name"] == "echo")
        assert echo["inputSchema"]["type"] == "object"
        assert "description" in echo

    @pytest.mark.asyncio
    async def test_hardcoded_excluded_tool_never_appears(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert "get_form_state" not in names

    def test_music_director_tools_are_on_the_exclusion_list(self):
        """Same reasoning as Video Director's pair: their result is only
        meaningful with a live frontend session's `form_state.music_director`
        -- an MCP caller has no analogous state to hand over."""
        assert "get_music_director" in EXCLUDED_TOOL_NAMES
        assert "update_music_director" in EXCLUDED_TOOL_NAMES

    @pytest.mark.asyncio
    async def test_governance_admin_disabled_tool_is_excluded(self, mcp_db):
        registry = ToolRegistry()
        registry.register(_EchoTool())
        governance_repo = ToolGovernanceRepository()
        governance_repo.upsert_config("cfg-default", "echo", enabled=False)
        llm_repository = Mock()
        llm_repository.get_default_configuration.return_value = SimpleNamespace(id="cfg-default")
        manager = McpProtocolManager(
            tool_registry=registry, tool_governance_repository=governance_repo, llm_repository=llm_repository,
        )
        result = await manager.handle_method("tools/list", {}, "user-1")
        assert result["tools"] == []

    @pytest.mark.asyncio
    async def test_user_opt_out_excludes_a_tool_for_that_user_only(self, mcp_db):
        registry = ToolRegistry()
        registry.register(_EchoTool())
        governance_repo = ToolGovernanceRepository()
        governance_repo.set_user_disabled("user-1", "echo", True)
        manager = McpProtocolManager(
            tool_registry=registry, tool_governance_repository=governance_repo, llm_repository=_no_default_config(),
        )
        mine = await manager.handle_method("tools/list", {}, "user-1")
        theirs = await manager.handle_method("tools/list", {}, "user-2")
        assert mine["tools"] == []
        assert {t["name"] for t in theirs["tools"]} == {"echo"}


class TestCallTool:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text_content(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method(
            "tools/call", {"name": "echo", "arguments": {"text": "hello"}}, "user-1"
        )
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "hello"}]

    @pytest.mark.asyncio
    async def test_unknown_tool_name_is_invalid_params(self, protocol):
        manager, _registry, _gov = protocol
        with pytest.raises(JsonRpcError) as exc:
            await manager.handle_method("tools/call", {"name": "does_not_exist"}, "user-1")
        assert exc.value.code == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_calling_an_excluded_tool_is_invalid_params(self, protocol):
        manager, _registry, _gov = protocol
        with pytest.raises(JsonRpcError) as exc:
            await manager.handle_method("tools/call", {"name": "get_form_state"}, "user-1")
        assert exc.value.code == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_missing_name_param_is_invalid_params(self, protocol):
        manager, _registry, _gov = protocol
        with pytest.raises(JsonRpcError) as exc:
            await manager.handle_method("tools/call", {}, "user-1")
        assert exc.value.code == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_a_raising_tool_comes_back_as_iserror_not_a_protocol_error(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("tools/call", {"name": "boom"}, "user-1")
        assert result["isError"] is True
        assert "kaboom" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_approval_gated_tool_runs_execute_confirmed_directly(self, protocol):
        manager, _registry, _gov = protocol
        result = await manager.handle_method("tools/call", {"name": "approve_me"}, "user-1")
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "applied!"}]


class TestRealOrganizationToolsOverMcp:
    """manage_collections/organize_gallery/start_generation, registered for
    real (not stubs), are exposed and callable end-to-end over MCP."""

    @pytest.fixture
    def real_protocol(self, mcp_db):
        from src.features.llm.tools.builtin import register_builtin_tools

        registry = ToolRegistry()
        register_builtin_tools(registry)
        governance_repo = ToolGovernanceRepository()
        collection_manager = Mock()
        manager = McpProtocolManager(
            tool_registry=registry,
            tool_governance_repository=governance_repo,
            llm_repository=_no_default_config(),
            collection_manager=collection_manager,
        )
        return manager, collection_manager

    @pytest.mark.asyncio
    async def test_all_three_appear_in_tools_list(self, real_protocol):
        manager, _collection_manager = real_protocol
        result = await manager.handle_method("tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert {"manage_collections", "organize_gallery", "start_generation"} <= names

    @pytest.mark.asyncio
    async def test_manage_collections_list_runs_end_to_end(self, real_protocol):
        manager, collection_manager = real_protocol
        collection = SimpleNamespace(to_dict=lambda: {"id": "col-1", "name": "Favorites"})
        collection_manager.list_collections.return_value = [collection]

        result = await manager.handle_method(
            "tools/call",
            {"name": "manage_collections", "arguments": {"operation": "list", "scope": "history"}},
            "user-1",
        )

        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["collections"] == [{"id": "col-1", "name": "Favorites"}]
        collection_manager.list_collections.assert_called_once_with("user-1", "history")
