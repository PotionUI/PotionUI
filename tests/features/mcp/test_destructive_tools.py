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


from src.features.chat.modes.builtin import (
    build_conversation_mode,
    build_elixirs_mode,
    build_generation_mode,
    build_history_mode,
    build_models_mode,
    build_phrasebook_mode,
    build_prompts_mode,
)
from src.features.llm.tools.builtin import register_builtin_tools
from tests.features.mcp.support import ADDED_AFTER_SEED


@pytest.fixture(scope="module")
def real_registry():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


class TestMcpOnlyTools:
    @pytest.mark.parametrize("build", [
        build_generation_mode, build_elixirs_mode, build_history_mode, build_models_mode,
        build_phrasebook_mode, build_prompts_mode, build_conversation_mode,
    ])
    def test_chat_never_sees_a_tool_added_for_mcp(self, real_registry, build):
        offered = {tool.name for tool in real_registry.get_for_mode(build())}

        assert offered.isdisjoint(ADDED_AFTER_SEED - {"list_models"})

    def test_the_new_tools_are_offered_to_the_mcp_mode_only(self, real_registry):
        added = ADDED_AFTER_SEED - {"list_models"}

        assert {tool.name for tool in real_registry.get_all() if tool.modes == ["mcp"]} == added

    def test_anything_destructive_admin_only_or_uploading_is_mcp_only(self, real_registry):
        risky = [
            tool for tool in real_registry.get_all()
            if tool.destructive or getattr(tool, "admin_only", False) or tool.name == "upload_media"
        ]

        assert risky and all(tool.modes == ["mcp"] for tool in risky)


class TestStateOfNewToolsAfterAnUpgrade:
    @pytest.mark.asyncio
    async def test_a_key_with_no_stored_choices_gets_no_destructive_tool_and_only_reads_under_read_only(self, mcp_db, real_registry):
        seed_user(mcp_db, "old")
        ApiKeyRepository().create(api_key(KEY_ID, "old"))
        llm = Mock()
        llm.get_default_configuration.return_value = None
        collaborators = build_collaborators(real_registry, ToolServices(llm_repository=llm))

        names = {t["name"] for t in (await handle_method(collaborators, "tools/list", {}, "old"))["tools"]}

        assert names.isdisjoint({"delete_generation", "delete_library_item", "cancel_generation", "upload_media", "run_elixir", "download_model", "publish_inspiration", "mark_notifications_read", "run_automation"})
        assert {"get_generation_status", "list_elixirs", "list_notifications"} <= names

    @pytest.mark.asyncio
    async def test_everything_on_still_withholds_every_destructive_tool(self, mcp_db, real_registry):
        seed_user(mcp_db, "all")
        McpToolRepository().apply_default("all", POLICY_ALL_ON, {})
        ApiKeyRepository().create(api_key(KEY_ID, "all"))
        llm = Mock()
        llm.get_default_configuration.return_value = None
        collaborators = build_collaborators(real_registry, ToolServices(llm_repository=llm))

        names = {t["name"] for t in (await handle_method(collaborators, "tools/list", {}, "all"))["tools"]}

        assert names.isdisjoint({"delete_generation", "delete_library_item"})
        assert "cancel_generation" in names
