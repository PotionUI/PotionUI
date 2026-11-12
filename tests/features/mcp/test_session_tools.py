import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin.list_sessions_tool import ListSessionsTool
from src.features.llm.tools.builtin.start_generation_tool import StartGenerationTool
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.catalog import build_catalog
from src.features.mcp.protocol import INVALID_PARAMS, JsonRpcError
from src.features.mcp.tool_policy import POLICY_ALL_ON, POLICY_READ_ONLY_ON
from src.features.mcp.tool_repository import McpToolRepository
from src.platform.security.user import AccountType
from tests.features.mcp.support import api_key, build_collaborators, handle_method
from tests.features.sessions.support import (
    PRESET,
    Models,
    Users,
    build_service,
    entry,
    memory_sessions,
    save,
    segment,
    template,
)
from tests.fixtures.seed import seed_user

USER = "user-1"


@pytest.fixture
def world(mcp_db, tmp_path):
    seed_user(mcp_db, USER)
    seed_user(mcp_db, "user-2")
    with memory_sessions() as (sessions, _):
        save(sessions, USER, session_id="mine", name="Portrait")
        save(sessions, "user-2", session_id="theirs", name="Other")
        service = build_service(
            sessions, str(tmp_path), template(),
            available={USER: [PRESET], "user-2": [PRESET]},
            users=Users(**{USER: AccountType.USER, "user-2": AccountType.USER}),
            models=Models({"m1": "One"}, {USER: ["m1"]}),
        )
        orchestrator = SimpleNamespace(
            start_generation=AsyncMock(return_value={"generation_id": "g9", "status": {"status": "pending"}})
        )
        registry = ToolRegistry()
        registry.register(ListSessionsTool())
        registry.register(StartGenerationTool())
        services = ToolServices(sessions=service, generation_orchestrator=orchestrator, settings=SimpleNamespace(get_setting=lambda key, default=None: default))
        yield SimpleNamespace(collaborators=build_collaborators(registry, services), orchestrator=orchestrator, registry=registry)


def policy(level):
    McpToolRepository().apply_default(USER, level, {})


async def listed(world, user=USER, key_id="key-1"):
    result = await handle_method(world.collaborators, "tools/list", {}, user, key_id=key_id)
    return {tool["name"]: tool for tool in result["tools"]}


async def call(world, name, arguments, user=USER):
    result = await handle_method(world.collaborators, "tools/call", {"name": name, "arguments": arguments}, user)
    return result["isError"], result["content"][0]["text"]


def test_the_catalogue_offers_list_sessions_as_a_read_tool_and_start_generation_still_spends(world):
    by_name = {entry.name: entry for entry in build_catalog(world.registry)}

    assert (by_name["list_sessions"].writes, by_name["list_sessions"].spends, by_name["list_sessions"].group) == (False, False, "Generation")
    assert (by_name["start_generation"].writes, by_name["start_generation"].spends) == (True, True)


class TestPolicy:
    @pytest.mark.asyncio
    async def test_read_only_policy_lists_sessions_but_not_the_spending_tool(self, world):
        policy(POLICY_READ_ONLY_ON)

        tools = await listed(world)

        assert "list_sessions" in tools and "start_generation" not in tools
        assert tools["list_sessions"]["annotations"] == {"readOnlyHint": True}
        assert (await call(world, "list_sessions", {}))[0] is False

    @pytest.mark.asyncio
    async def test_starting_from_a_session_is_refused_under_read_only_and_never_reaches_the_orchestrator(self, world):
        policy(POLICY_READ_ONLY_ON)

        with pytest.raises(JsonRpcError) as refused:
            await handle_method(world.collaborators, "tools/call", {"name": "start_generation", "arguments": {"session_id": "mine"}}, USER)

        assert refused.value.code == INVALID_PARAMS and "turned off" in refused.value.message
        world.orchestrator.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_on_offers_both_and_the_start_schema_takes_a_session(self, world):
        policy(POLICY_ALL_ON)

        tools = await listed(world)

        schema = tools["start_generation"]["inputSchema"]
        assert {"list_sessions", "start_generation"} <= set(tools)
        assert {"session_id", "seed", "count", "variables", "preset_id", "form_overrides"} <= set(schema["properties"])
        assert "preset_id" not in schema["required"]
        assert "session_id" in tools["start_generation"]["description"]

    @pytest.mark.asyncio
    async def test_a_key_that_narrowed_the_start_tool_cannot_start_from_a_session(self, world, mcp_db):
        from tests.features.api_keys.conftest import Stack

        policy(POLICY_ALL_ON)
        key = Stack().keys.create(api_key("narrow", USER))
        McpToolRepository().apply_key_overrides(key.id, ["start_generation"], [])

        tools = await listed(world, key_id=key.id)
        with pytest.raises(JsonRpcError):
            await handle_method(world.collaborators, "tools/call", {"name": "start_generation", "arguments": {"session_id": "mine"}}, USER, key_id=key.id)

        assert list(tools) == ["list_sessions"]
        world.orchestrator.start_generation.assert_not_called()


class TestListSessions:
    @pytest.mark.asyncio
    async def test_lists_only_the_users_sessions(self, world):
        policy(POLICY_READ_ONLY_ON)

        error, text = await call(world, "list_sessions", {})

        data = json.loads(text)
        assert error is False and [item["id"] for item in data["sessions"]] == ["mine"]
        assert data["sessions"][0]["modes"] == ["txt2img"] and data["sessions"][0]["prompts"]["txt2img"] == "a lighthouse"

    @pytest.mark.asyncio
    async def test_describes_one_session_without_the_resolved_request(self, world):
        policy(POLICY_READ_ONLY_ON)

        error, text = await call(world, "list_sessions", {"session_id": "mine"})

        data = json.loads(text)
        assert error is False and "resolved_request" not in data
        assert {item["name"] for item in data["fields"]} >= {"seed", "quantity", "steps"}
        assert data["session"]["settings_hash"] and data["shortcuts"]["count"] == "quantity"

    @pytest.mark.asyncio
    async def test_someone_elses_session_is_just_not_found(self, world):
        policy(POLICY_READ_ONLY_ON)

        error, text = await call(world, "list_sessions", {"session_id": "theirs"})

        assert error is True and "Session not found" in text


class TestStartFromSession:
    @pytest.mark.asyncio
    async def test_starts_through_the_orchestrator_with_the_resolved_request(self, world):
        policy(POLICY_ALL_ON)

        error, text = await call(world, "start_generation", {
            "session_id": "mine", "prompt": "a glass bottle", "seed": 3, "count": 2, "form_overrides": {"steps": 12},
        })

        data = json.loads(text)
        request = world.orchestrator.start_generation.await_args.kwargs["request"]
        assert error is False and data["generation_id"] == "g9" and data["session"]["id"] == "mine"
        assert request.preset_id == PRESET and request.mode == "txt2img"
        assert request.prompts[0].positive == "a glass bottle" and request.prompts[0].negative == "blur"
        assert request.form_data["seed"] == 3 and request.form_data["quantity"] == 2 and request.form_data["steps"] == 12
        assert world.orchestrator.start_generation.await_args.kwargs["user_id"] == USER

    @pytest.mark.asyncio
    async def test_session_id_and_preset_id_are_mutually_exclusive(self, world):
        policy(POLICY_ALL_ON)

        error, text = await call(world, "start_generation", {"session_id": "mine", "preset_id": PRESET})

        assert error is True and "not both" in text
        world.orchestrator.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_refusals_come_back_as_tool_errors_that_name_the_problem(self, world):
        policy(POLICY_ALL_ON)

        unknown = await call(world, "start_generation", {"session_id": "mine", "form_overrides": {"cfg": 1}})
        foreign = await call(world, "start_generation", {"session_id": "theirs"})
        mixed = await call(world, "start_generation", {"session_id": "mine", "prompt": "a", "prompts": [{"positive": "b"}]})

        assert unknown[0] and "no field named cfg" in unknown[1]
        assert foreign[0] and "Session not found" in foreign[1]
        assert mixed[0]
        world.orchestrator.start_generation.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_plain_preset_call_is_unchanged(self, world):
        policy(POLICY_ALL_ON)

        error, text = await call(world, "start_generation", {"preset_id": PRESET, "prompt": "a red fox"})

        assert error is False and json.loads(text)["generation_id"] == "g9" and "session" not in json.loads(text)
        assert world.orchestrator.start_generation.await_args.kwargs["request"].prompts[0].positive == "a red fox"

    @pytest.mark.asyncio
    async def test_a_session_with_a_kit_citation_hands_it_to_the_orchestrator_verbatim(self, world):
        policy(POLICY_ALL_ON)
        cite = "@[references:kit:k1/i1]"
        save(world.collaborators.services.sessions.sessions, USER, session_id="kit", name="Kit", data={"txt2img": entry(promptSegments=[segment("a", f"{cite} smiles")], prompt="")})

        await call(world, "start_generation", {"session_id": "kit"})

        assert world.orchestrator.start_generation.await_args.kwargs["request"].prompts[0].positive == f"{cite} smiles"
