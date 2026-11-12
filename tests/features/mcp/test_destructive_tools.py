from unittest.mock import Mock

import pytest

from src.features.llm.tools.base import BaseTool, ToolResult, ToolServices
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.mappers import tool_to_dict
from src.features.mcp.catalog import build_catalog
from src.features.mcp.operations import tools as tool_operations
from src.features.mcp.protocol import JsonRpcError
from src.features.mcp.tool_policy import (
    POLICY_ALL_ON,
    POLICY_READ_ONLY_ON,
    LevelPolicy,
    ToolState,
    allows,
    ceiling_state,
    level_allows,
    resolve_state,
)
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import KEY_ID, api_key, build_collaborators, handle_method
from tests.fixtures.seed import seed_user
from src.features.api_keys.repository import ApiKeyRepository

NONE = frozenset()


class TestPolicyArithmetic:
    @pytest.mark.parametrize("policy", [POLICY_ALL_ON, POLICY_READ_ONLY_ON])
    def test_a_destructive_tool_is_never_allowed_by_a_policy_alone(self, policy):
        assert allows(policy, True, False, True) is False

    def test_an_explicit_override_is_what_turns_a_destructive_tool_on(self):
        level = LevelPolicy(POLICY_ALL_ON, {"wipe": True})

        assert level_allows(level, "wipe", True, False, True) is True

    def test_a_tool_that_is_not_destructive_is_unchanged(self):
        assert allows(POLICY_ALL_ON, True, False, False) is True

    def test_without_an_admin_choice_the_tool_is_locked(self):
        state = resolve_state(
            "wipe", True, None, LevelPolicy(POLICY_ALL_ON), LevelPolicy(POLICY_ALL_ON, {"wipe": True}), NONE,
            destructive=True,
        )

        assert state == ToolState.LOCKED_BY_ADMIN

    def test_with_the_admin_choice_but_no_user_choice_it_is_off_for_the_user(self):
        state = resolve_state(
            "wipe", True, None, LevelPolicy(POLICY_ALL_ON, {"wipe": True}), LevelPolicy(POLICY_ALL_ON), NONE,
            destructive=True,
        )

        assert state == ToolState.OFF_IN_DEFAULT

    def test_with_both_choices_it_is_on_and_a_key_can_still_switch_it_off(self):
        ceiling = LevelPolicy(POLICY_ALL_ON, {"wipe": True})
        default = LevelPolicy(POLICY_ALL_ON, {"wipe": True})

        assert resolve_state("wipe", True, None, ceiling, default, NONE, destructive=True) == ToolState.ON
        assert resolve_state("wipe", True, None, ceiling, default, frozenset({"wipe"}), destructive=True) == ToolState.OFF

    def test_the_administrator_screen_shows_it_off_until_chosen(self):
        assert ceiling_state("wipe", True, None, LevelPolicy(POLICY_ALL_ON), destructive=True) == ToolState.OFF
        assert ceiling_state("wipe", True, None, LevelPolicy(POLICY_ALL_ON, {"wipe": True}), destructive=True) == ToolState.ON


class _Wipe(BaseTool):
    name = "wipe"
    mcp = True
    destructive = True
    group = "Generation"

    @property
    def description(self):
        return "wipe"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="wiped")


class _Add(_Wipe):
    name = "add"
    destructive = False


@pytest.fixture
def collaborators(mcp_db):
    seed_user(mcp_db, "u1")
    McpToolRepository().apply_default("u1", POLICY_ALL_ON, {})
    ApiKeyRepository().create(api_key(KEY_ID, "u1"))
    registry = ToolRegistry()
    registry.register(_Wipe())
    registry.register(_Add())
    llm_repository = Mock()
    llm_repository.get_default_configuration.return_value = None
    return build_collaborators(registry, ToolServices(llm_repository=llm_repository))


async def listed(collaborators):
    result = await handle_method(collaborators, "tools/list", {}, "u1")
    return {tool["name"]: tool for tool in result["tools"]}


class TestThroughTheProtocol:
    @pytest.mark.asyncio
    async def test_everything_on_still_leaves_a_destructive_tool_off(self, collaborators):
        assert set(await listed(collaborators)) == {"add"}

    @pytest.mark.asyncio
    async def test_a_call_to_it_is_refused_as_locked(self, collaborators):
        with pytest.raises(JsonRpcError, match="turned off by your administrator"):
            await handle_method(collaborators, "tools/call", {"name": "wipe", "arguments": {}}, "u1")

    @pytest.mark.asyncio
    async def test_the_admin_choice_alone_is_not_enough(self, collaborators):
        McpToolRepository().apply_ceiling({"wipe": True}, None)

        assert set(await listed(collaborators)) == {"add"}

    @pytest.mark.asyncio
    async def test_both_choices_make_it_available_and_it_says_it_is_destructive(self, collaborators):
        McpToolRepository().apply_ceiling({"wipe": True}, None)
        McpToolRepository().apply_default("u1", POLICY_ALL_ON, {"wipe": True})

        tools = await listed(collaborators)
        result = await handle_method(collaborators, "tools/call", {"name": "wipe", "arguments": {}}, "u1")

        assert tools["wipe"]["annotations"] == {"readOnlyHint": False, "destructiveHint": True}
        assert "destructiveHint" not in tools["add"]["annotations"]
        assert result["content"][0]["text"] == "wiped"

    @pytest.mark.asyncio
    async def test_the_key_can_switch_it_off_again(self, collaborators):
        McpToolRepository().apply_ceiling({"wipe": True}, None)
        McpToolRepository().apply_default("u1", POLICY_ALL_ON, {"wipe": True})
        McpToolRepository().apply_key_overrides(KEY_ID, ["wipe"], [])

        assert set(await listed(collaborators)) == {"add"}

    @pytest.mark.asyncio
    async def test_the_default_screen_cannot_enable_it_over_the_ceiling(self, collaborators):
        with pytest.raises(Exception, match="turned off by your administrator"):
            tool_operations.patch_default(collaborators, "u1", None, {"wipe": True})

    @pytest.mark.asyncio
    async def test_the_listing_flags_it_for_the_screen(self, collaborators):
        listing = tool_operations.default_listing(collaborators, "u1")

        flags = {tool["name"]: tool["destructive"] for tool in listing["tools"]}
        assert flags == {"wipe": True, "add": False}

    def test_the_catalogue_carries_the_flag(self, collaborators):
        entries = {entry.name: entry for entry in build_catalog(collaborators.tool_registry)}

        assert entries["wipe"].destructive is True and entries["add"].destructive is False
        assert tool_to_dict(entries["wipe"], ToolState.OFF, False)["destructive"] is True
