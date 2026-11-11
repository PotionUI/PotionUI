from src.features.mcp.operations.tools import counts_for_keys
from src.features.mcp.tool_repository import McpToolRepository
from tests.features.api_keys.conftest import Stack
from tests.features.mcp.support import api_key, build_collaborators
from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.registry import ToolRegistry


def _tool(tool_name, read_only, unavailable=None):
    class _Tool(BaseTool):
        name = tool_name
        mcp = True
        mcp_unavailable = unavailable

        @property
        def description(self):
            return tool_name

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, context, **kwargs):
            return ToolResult(success=True, data="")

    _Tool.read_only = read_only
    return _Tool()


def test_counts_are_per_key_not_per_owner(keys_db):
    stack = Stack()
    alice = stack.user("alice")
    registry = ToolRegistry()
    for tool in (_tool("read_a", True), _tool("read_b", True), _tool("write_c", False), _tool("live_d", True, "live form")):
        registry.register(tool)
    collaborators = build_collaborators(registry)
    stack.keys.create(api_key("narrow", alice.id))
    stack.keys.create(api_key("wide", alice.id))
    McpToolRepository().apply_key_overrides("narrow", ["read_a"], [])

    counts = counts_for_keys(collaborators, [("narrow", alice.id), ("wide", alice.id)])

    assert counts == {"narrow": (1, 3), "wide": (2, 3)}


def test_a_key_follows_its_owners_default(keys_db):
    stack = Stack()
    alice, bob = stack.user("alice"), stack.user("bob")
    registry = ToolRegistry()
    for tool in (_tool("read_a", True), _tool("write_c", False)):
        registry.register(tool)
    collaborators = build_collaborators(registry)
    stack.keys.create(api_key("alices", alice.id))
    stack.keys.create(api_key("bobs", bob.id))
    McpToolRepository().apply_default(bob.id, "all_on", {})

    counts = counts_for_keys(collaborators, [("alices", alice.id), ("bobs", bob.id)])

    assert counts == {"alices": (1, 2), "bobs": (2, 2)}


def test_the_administrator_ceiling_removes_a_tool_from_the_count_and_the_total(keys_db):
    stack = Stack()
    alice = stack.user("alice")
    registry = ToolRegistry()
    for tool in (_tool("read_a", True), _tool("read_b", True)):
        registry.register(tool)
    collaborators = build_collaborators(registry)
    stack.keys.create(api_key("k", alice.id))
    McpToolRepository().apply_ceiling({"read_b": False}, None)

    assert counts_for_keys(collaborators, [("k", alice.id)]) == {"k": (1, 1)}


def test_no_keys_means_no_lookups(keys_db):
    assert counts_for_keys(build_collaborators(ToolRegistry()), []) == {}
