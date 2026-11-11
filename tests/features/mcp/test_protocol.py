import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import BaseTool, ToolResult, ToolServices
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JsonRpcError,
    parse_jsonrpc_request,
)
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import api_key, build_collaborators, handle_method, user_with_every_tool
from tests.fixtures.seed import seed_user


class _EchoTool(BaseTool):
    modes = ["generation"]
    mcp = True
    read_only = True

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
    mcp = True
    read_only = True

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
    mcp = True

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
    modes = ["generation"]
    mcp_unavailable = "Needs the live Generate form"

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
    user_with_every_tool(mcp_db, "user-1")
    seed_user(mcp_db, "user-2")
    collaborators = build_collaborators(registry, ToolServices(llm_repository=_no_default_config()))
    return collaborators, registry, McpToolRepository()


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
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "initialize", {"protocolVersion": "2025-06-18"}, "user-1")
        assert result["protocolVersion"] == "2025-06-18"
        assert result["capabilities"] == {"tools": {}}
        assert result["serverInfo"]["name"] == "potionui"

    @pytest.mark.asyncio
    async def test_initialize_falls_back_to_default_version_for_an_unknown_request(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "initialize", {"protocolVersion": "1999-01-01"}, "user-1")
        assert result["protocolVersion"] == "2025-06-18"

    @pytest.mark.asyncio
    async def test_notifications_initialized_returns_none(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "notifications/initialized", {}, "user-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_returns_empty_object(self, protocol):
        collaborators, _registry, _repo = protocol
        assert await handle_method(collaborators, "ping", {}, "user-1") == {}

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self, protocol):
        collaborators, _registry, _repo = protocol
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "nonexistent/method", {}, "user-1")
        assert exc.value.code == METHOD_NOT_FOUND


class TestListTools:
    @pytest.mark.asyncio
    async def test_normal_tools_are_listed_with_mcp_shaped_schema(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert "echo" in names
        echo = next(t for t in result["tools"] if t["name"] == "echo")
        assert echo["inputSchema"]["type"] == "object"
        assert "description" in echo

    @pytest.mark.asyncio
    async def test_a_tool_that_cannot_run_over_mcp_never_appears(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert "get_form_state" not in names

    @pytest.mark.asyncio
    async def test_each_tool_carries_a_title_and_the_read_only_hint(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/list", {}, "user-1")
        by_name = {t["name"]: t for t in result["tools"]}
        assert by_name["echo"]["title"] == "Echo"
        assert by_name["echo"]["annotations"] == {"readOnlyHint": True}
        assert by_name["approve_me"]["annotations"] == {"readOnlyHint": False}

    @pytest.mark.asyncio
    async def test_a_tool_the_administrator_turned_off_is_not_listed(self, protocol):
        collaborators, _registry, repo = protocol
        repo.apply_ceiling({"echo": False}, None)
        result = await handle_method(collaborators, "tools/list", {}, "user-1")
        assert "echo" not in {t["name"] for t in result["tools"]}

    @pytest.mark.asyncio
    async def test_a_user_default_applies_to_that_user_only(self, protocol):
        collaborators, _registry, repo = protocol
        repo.apply_default("user-1", None, {"echo": False})
        mine = await handle_method(collaborators, "tools/list", {}, "user-1")
        theirs = await handle_method(collaborators, "tools/list", {}, "user-2")
        assert "echo" not in {t["name"] for t in mine["tools"]}
        assert {t["name"] for t in theirs["tools"]} == {"echo", "boom"}

    @pytest.mark.asyncio
    async def test_a_key_narrowing_applies_to_that_key_only(self, protocol, mcp_db):
        from tests.features.api_keys.conftest import Stack

        collaborators, _registry, repo = protocol
        keys = Stack()
        owner = keys.users.get_by_id("user-1")
        first = keys.keys.create(api_key("key-a", owner.id))
        second = keys.keys.create(api_key("key-b", owner.id))
        repo.apply_key_overrides(first.id, ["echo"], [])
        narrowed = await handle_method(collaborators, "tools/list", {}, "user-1", key_id=first.id)
        other = await handle_method(collaborators, "tools/list", {}, "user-1", key_id=second.id)
        assert "echo" not in {t["name"] for t in narrowed["tools"]}
        assert "echo" in {t["name"] for t in other["tools"]}

    @pytest.mark.asyncio
    async def test_a_writing_tool_is_off_until_the_user_turns_writes_on(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/list", {}, "user-2")
        assert "approve_me" not in {t["name"] for t in result["tools"]}


class TestCallTool:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text_content(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(
            collaborators, "tools/call", {"name": "echo", "arguments": {"text": "hello"}}, "user-1"
        )
        assert result["isError"] is False
        assert result["content"] == [{"type": "text", "text": "hello"}]

    @pytest.mark.asyncio
    async def test_unknown_tool_name_is_invalid_params(self, protocol):
        collaborators, _registry, _repo = protocol
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {"name": "does_not_exist"}, "user-1")
        assert exc.value.code == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_calling_a_tool_that_cannot_run_over_mcp_names_the_reason(self, protocol):
        collaborators, _registry, _repo = protocol
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {"name": "get_form_state"}, "user-1")
        assert exc.value.code == INVALID_PARAMS
        assert exc.value.message == "Tool 'get_form_state' can't run over MCP: Needs the live Generate form"

    @pytest.mark.asyncio
    async def test_calling_a_tool_turned_off_for_the_key_says_so(self, protocol):
        collaborators, _registry, repo = protocol
        repo.apply_default("user-1", None, {"echo": False})
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {"name": "echo", "arguments": {"text": "x"}}, "user-1")
        assert exc.value.code == INVALID_PARAMS
        assert exc.value.message == "Tool 'echo' is turned off for this key"

    @pytest.mark.asyncio
    async def test_calling_a_tool_the_administrator_turned_off_says_so(self, protocol):
        collaborators, _registry, repo = protocol
        repo.apply_ceiling({"echo": False}, None)
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {"name": "echo", "arguments": {"text": "x"}}, "user-1")
        assert exc.value.message == "Tool 'echo' is turned off by your administrator"

    @pytest.mark.asyncio
    async def test_a_write_tool_is_refused_while_the_default_is_read_only(self, protocol):
        collaborators, _registry, _repo = protocol
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {"name": "approve_me"}, "user-2")
        assert exc.value.message == "Tool 'approve_me' is turned off for this key"

    @pytest.mark.asyncio
    async def test_missing_name_param_is_invalid_params(self, protocol):
        collaborators, _registry, _repo = protocol
        with pytest.raises(JsonRpcError) as exc:
            await handle_method(collaborators, "tools/call", {}, "user-1")
        assert exc.value.code == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_a_raising_tool_comes_back_as_iserror_not_a_protocol_error(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/call", {"name": "boom"}, "user-1", is_admin=True)
        assert result["isError"] is True
        assert "kaboom" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_raising_tool_hides_its_exception_text_from_a_regular_user(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/call", {"name": "boom"}, "user-1")
        assert result["isError"] is True
        assert "kaboom" not in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_approval_gated_tool_runs_execute_confirmed_directly(self, protocol):
        collaborators, _registry, _repo = protocol
        result = await handle_method(collaborators, "tools/call", {"name": "approve_me"}, "user-1")
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
        user_with_every_tool(mcp_db, "user-1")
        collection_repository = Mock()
        collaborators = build_collaborators(registry, ToolServices(
            llm_repository=_no_default_config(),
            collection_repository=collection_repository,
        ))
        return collaborators, collection_repository

    @pytest.mark.asyncio
    async def test_all_three_appear_in_tools_list(self, real_protocol):
        collaborators, _collection_repository = real_protocol
        result = await handle_method(collaborators, "tools/list", {}, "user-1")
        names = {t["name"] for t in result["tools"]}
        assert {"manage_collections", "organize_gallery", "start_generation"} <= names

    @pytest.mark.asyncio
    async def test_manage_collections_list_runs_end_to_end(self, real_protocol):
        collaborators, collection_repository = real_protocol
        collection = SimpleNamespace(to_dict=lambda: {"id": "col-1", "name": "Favorites"})
        collection_repository.list.return_value = [collection]

        result = await handle_method(
            collaborators, "tools/call",
            {"name": "manage_collections", "arguments": {"operation": "list", "scope": "history"}},
            "user-1",
        )

        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["collections"] == [{"id": "col-1", "name": "Favorites"}]
        collection_repository.list.assert_called_once_with("user-1", "history")


class TestModelVisibilityOverMcp:
    """MCP acts in the name of the token's owner and must see exactly what that
    user sees, so list_models/get_model_info must apply the same
    ModelAccessPolicy the regular /api/models listing enforces
    (`ModelIndexCollaborators.access`) rather than handing every caller the
    unfiltered catalog."""

    @pytest.fixture
    def model_protocol(self, mcp_db):
        from src.features.llm.tools.builtin import register_builtin_tools
        from src.features.models.access_policy import ModelAccessPolicy
        from src.features.models.catalog import ModelCatalog
        from src.features.models.records import Model
        from src.features.models.repository import ModelRepository

        model_repo = ModelRepository()
        model = model_repo.create(Model(
            filename="unassigned.safetensors",
            file_size=1024,
            sha256="a" * 64,
            model_type="checkpoint",
        ))
        for user_id, account_type in (("user-1", "USER"), ("admin-1", "ADMIN")):
            seed_user(mcp_db, user_id, account_type=account_type)

        registry = ToolRegistry()
        register_builtin_tools(registry)
        access_policy = ModelAccessPolicy(model_repo)
        model_index_manager = SimpleNamespace(
            model_repo=model_repo,
            access=access_policy,
            catalog=ModelCatalog(model_repo, access_policy, scanner=None),
        )
        collaborators = build_collaborators(registry, ToolServices(
            llm_repository=_no_default_config(),
            model_index_manager=model_index_manager,
        ))
        return collaborators, model, model_repo

    @pytest.mark.asyncio
    async def test_search_models_hides_an_unassigned_model_from_a_normal_user(self, model_protocol):
        collaborators, _model, _repo = model_protocol
        result = await handle_method(
            collaborators, "tools/call", {"name": "search_models", "arguments": {}}, "user-1",
        )
        payload = json.loads(result["content"][0]["text"])
        assert payload["models"] == []

    @pytest.mark.asyncio
    async def test_search_models_shows_the_model_once_assigned(self, model_protocol):
        collaborators, model, repo = model_protocol
        repo.assign_model_to_user(model.id, "user-1")

        result = await handle_method(
            collaborators, "tools/call", {"name": "search_models", "arguments": {}}, "user-1",
        )
        payload = json.loads(result["content"][0]["text"])
        assert [m["id"] for m in payload["models"]] == [model.id]

    @pytest.mark.asyncio
    async def test_search_models_shows_admin_everything_regardless_of_assignment(self, model_protocol):
        collaborators, model, _repo = model_protocol
        result = await handle_method(
            collaborators, "tools/call", {"name": "search_models", "arguments": {}}, "admin-1",
            is_admin=True,
        )
        payload = json.loads(result["content"][0]["text"])
        assert [m["id"] for m in payload["models"]] == [model.id]

    @pytest.mark.asyncio
    async def test_get_model_info_reports_an_unassigned_model_not_found_to_a_normal_user(self, model_protocol):
        collaborators, model, _repo = model_protocol
        result = await handle_method(
            collaborators, "tools/call", {"name": "get_model_info", "arguments": {"model_id": model.id}}, "user-1",
        )
        assert result["isError"] is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_get_model_info_succeeds_once_assigned(self, model_protocol):
        collaborators, model, repo = model_protocol
        repo.assign_model_to_user(model.id, "user-1")

        result = await handle_method(
            collaborators, "tools/call", {"name": "get_model_info", "arguments": {"model_id": model.id}}, "user-1",
        )
        assert result["isError"] is False
        payload = json.loads(result["content"][0]["text"])
        assert payload["id"] == model.id


class TestPromptReadTools:
    @pytest.mark.asyncio
    async def test_tools_list_includes_both_and_get_prompt_is_callable(self, mcp_db):
        from src.features.llm.tools.builtin.manage_prompts_tool import GetPromptTool, ListPromptsTool
        from src.features.prompt_database.records import Prompt
        from src.features.prompt_database.repository import PromptRepository
        from src.features.segments.dto import RichSegment

        registry = ToolRegistry()
        registry.register(GetPromptTool())
        registry.register(ListPromptsTool())
        for user_id in ("user-1", "user-2"):
            seed_user(mcp_db, user_id)
        repository = PromptRepository()
        saved = repository.create(Prompt(user_id="user-1", name="Study", segments=[RichSegment(content="a fox")]))
        collaborators = build_collaborators(registry, ToolServices(
            llm_repository=_no_default_config(),
            prompt_database=SimpleNamespace(repository=repository),
        ))

        listed = await handle_method(collaborators, "tools/list", {}, "user-1")
        called = await handle_method(
            collaborators, "tools/call",
            {"name": "get_prompt", "arguments": {"prompt_id": saved.id}}, "user-1",
        )
        foreign = await handle_method(
            collaborators, "tools/call",
            {"name": "get_prompt", "arguments": {"prompt_id": saved.id}}, "user-2",
        )

        assert {"get_prompt", "list_prompts"} <= {t["name"] for t in listed["tools"]}
        payload = json.loads(called["content"][0]["text"])
        assert payload["prompt_id"] == saved.id
        assert payload["segments"][0]["content"] == "a fox"
        assert foreign.get("isError") is True
