from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import ToolServices
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.governance import resolve_tool_scope
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import api_key, build_collaborators, handle_method, user_with_every_tool

CONFIG_ID = "cfg-default"
CHAT_MODE = SimpleNamespace(id="generation", tool_names=None, offers_tools=True)


@pytest.fixture
def world(mcp_db):
    registry = ToolRegistry()
    register_builtin_tools(registry)
    user_with_every_tool(mcp_db, "user-1")
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = SimpleNamespace(id=CONFIG_ID)
    return SimpleNamespace(
        registry=registry,
        collaborators=build_collaborators(registry, ToolServices(llm_repository=llm_repository)),
        governance=ToolGovernanceRepository(),
        mcp=McpToolRepository(),
    )


async def listed(world):
    result = await handle_method(world.collaborators, "tools/list", {}, "user-1")
    return sorted(tool["name"] for tool in result["tools"])


def chat_offers(world):
    tools = world.registry.get_for_mode(CHAT_MODE)
    return sorted(resolve_tool_scope(tools, world.governance, CONFIG_ID, "user-1").allowed_names())


class TestChatSettingsNoLongerChangeMcp:
    @pytest.mark.asyncio
    async def test_an_administrator_turning_tools_off_in_chat_leaves_tools_list_alone(self, world):
        before = await listed(world)

        for name in ("start_generation", "list_presets", "write_memory", "search_gallery"):
            world.governance.upsert_config(CONFIG_ID, name, enabled=False)

        assert await listed(world) == before
        assert "start_generation" in before

    @pytest.mark.asyncio
    async def test_a_users_chat_opt_outs_leave_tools_list_alone(self, world):
        before = await listed(world)

        for name in ("get_prompt", "list_prompts", "write_condition"):
            world.governance.set_user_disabled("user-1", name, True)

        assert await listed(world) == before
        assert "get_prompt" in before

    @pytest.mark.asyncio
    async def test_a_tool_the_chat_governance_turned_off_still_runs_over_mcp(self, world):
        world.governance.upsert_config(CONFIG_ID, "write_condition", enabled=False)
        arguments = {"branches": [{"tests": [{"var": "age", "op": ">", "value": 65}], "text": "grey hair"}]}

        result = await handle_method(
            world.collaborators, "tools/call", {"name": "write_condition", "arguments": arguments}, "user-1"
        )

        assert result["isError"] is False

    @pytest.mark.asyncio
    async def test_tools_list_does_not_ask_the_chat_governance_for_anything(self, world, monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("the chat governance was read")

        monkeypatch.setattr(ToolGovernanceRepository, "get_config_snapshot", refuse)
        monkeypatch.setattr(ToolGovernanceRepository, "get_user_disabled", refuse)

        assert await listed(world)


class TestMcpSettingsNoLongerChangeChat:
    @pytest.mark.asyncio
    async def test_the_administrator_ceiling_leaves_what_chat_offers_alone(self, world):
        before = chat_offers(world)

        world.mcp.apply_ceiling({"start_generation": False, "list_presets": False, "write_memory": False}, None)
        world.collaborators.settings.set_setting("mcp_tools_ceiling_policy", "read_only_on")

        assert chat_offers(world) == before
        assert "start_generation" in before and "write_memory" in before

    @pytest.mark.asyncio
    async def test_a_users_mcp_default_leaves_what_chat_offers_alone(self, world):
        before = chat_offers(world)

        world.mcp.apply_default("user-1", "read_only_on", {"list_presets": False, "get_prompt": False})

        assert chat_offers(world) == before
        assert "list_presets" in before

    @pytest.mark.asyncio
    async def test_a_key_narrowing_leaves_what_chat_offers_alone(self, world):
        from tests.features.api_keys.conftest import Stack
        stack = Stack()
        stack.keys.create(api_key("key-narrow", "user-1"))
        before = chat_offers(world)

        world.mcp.apply_key_overrides("key-narrow", ["list_presets", "search_models"], [])

        assert chat_offers(world) == before

    @pytest.mark.asyncio
    async def test_the_mcp_changes_do_change_tools_list(self, world):
        before = await listed(world)

        world.mcp.apply_default("user-1", None, {"list_presets": False})

        assert await listed(world) == sorted(set(before) - {"list_presets"})
