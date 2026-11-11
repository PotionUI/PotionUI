import pytest

from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.operations import tools as tool_operations
from src.features.mcp.protocol import INVALID_PARAMS, JsonRpcError
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.mcp.support import build_collaborators, handle_method
from tests.fixtures.seed import seed_user


def _plugin_tool(tool_name, mcp, read_only):
    class _PluginTool(BaseTool):
        modes = None

        @property
        def description(self):
            return tool_name

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, context, **kwargs):
            return ToolResult(success=True, data=f"ran {tool_name}")

    _PluginTool.name = tool_name
    _PluginTool.mcp = mcp
    _PluginTool.read_only = read_only
    return _PluginTool()


@pytest.fixture
def world(mcp_db):
    seed_user(mcp_db, "user-1")
    registry = ToolRegistry()
    registry.register(_plugin_tool("plugin_hidden", False, True), source="acme")
    registry.register(_plugin_tool("plugin_reads", True, True), source="acme")
    registry.register(_plugin_tool("plugin_writes", True, False), source="acme")
    return registry, build_collaborators(registry)


async def listed(collaborators):
    result = await handle_method(collaborators, "tools/list", {}, "user-1")
    return {tool["name"] for tool in result["tools"]}


@pytest.mark.asyncio
async def test_a_plugin_tool_that_does_not_declare_mcp_is_absent_and_unknown(world):
    _registry, collaborators = world

    assert "plugin_hidden" not in await listed(collaborators)
    with pytest.raises(JsonRpcError) as exc:
        await handle_method(collaborators, "tools/call", {"name": "plugin_hidden"}, "user-1")
    assert exc.value.code == INVALID_PARAMS
    assert exc.value.message == "Unknown tool: plugin_hidden"
    assert "plugin_hidden" not in {t["name"] for t in tool_operations.default_listing(collaborators, "user-1")["tools"]}


@pytest.mark.asyncio
async def test_a_plugin_tool_that_declares_mcp_arrives_new_and_on_if_it_reads(world):
    _registry, collaborators = world

    listing = {t["name"]: t for t in tool_operations.default_listing(collaborators, "user-1")["tools"]}

    assert (listing["plugin_reads"]["state"], listing["plugin_reads"]["reviewed"]) == ("on", False)
    assert (listing["plugin_writes"]["state"], listing["plugin_writes"]["reviewed"]) == ("off", False)
    assert listing["plugin_writes"]["writes"] is True
    assert await listed(collaborators) == {"plugin_reads"}


@pytest.mark.asyncio
async def test_a_plugin_tool_is_on_at_the_ceiling_under_the_default_policy(world):
    _registry, collaborators = world

    listing = {t["name"]: t["state"] for t in tool_operations.ceiling_listing(collaborators)["tools"]}

    assert listing == {"plugin_reads": "on", "plugin_writes": "on"}


@pytest.mark.asyncio
async def test_a_plugin_write_tool_runs_once_the_user_turns_it_on(world, mcp_db):
    _registry, collaborators = world
    McpToolRepository().apply_default("user-1", None, {"plugin_writes": True})

    result = await handle_method(collaborators, "tools/call", {"name": "plugin_writes"}, "user-1")

    assert result["content"][0]["text"] == "ran plugin_writes"


@pytest.mark.asyncio
async def test_disabling_the_plugin_removes_its_tools_and_the_choices_survive(world):
    registry, collaborators = world
    McpToolRepository().apply_default("user-1", None, {"plugin_writes": True, "plugin_reads": False})

    registry.unregister_source("acme")
    assert await listed(collaborators) == set()
    assert tool_operations.default_listing(collaborators, "user-1")["tools"] == []

    registry.register(_plugin_tool("plugin_reads", True, True), source="acme")
    registry.register(_plugin_tool("plugin_writes", True, False), source="acme")
    assert await listed(collaborators) == {"plugin_writes"}

