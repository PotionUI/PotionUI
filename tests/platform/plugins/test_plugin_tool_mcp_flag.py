from types import SimpleNamespace

import pytest

from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.registry import ToolRegistry
from tests.platform.plugins.test_registry_sections import make_registry


def _tool_class(declares_mcp):
    class _Tool(BaseTool):
        name = "plugin_tool"
        mcp = declares_mcp

        @property
        def description(self):
            return "d"

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, context, **kwargs):
            return ToolResult(success=True, data="")

    return _Tool


def _register(tmp_path, tool_class, entry):
    tools = ToolRegistry()
    registry = make_registry(tmp_path, tool_registry=tools)
    registry.loader.load_class = lambda manifest, ref: tool_class
    manifest = SimpleNamespace(id="acme", tools=[entry])

    assert registry._register_plugin_tools(manifest) is None
    return tools.get("plugin_tool")


@pytest.mark.parametrize("declared, entry, expected", [
    (False, {"class": "m.T"}, False),
    (True, {"class": "m.T"}, True),
    (False, {"class": "m.T", "mcp": True}, True),
    (True, {"class": "m.T", "mcp": False}, False),
    (True, {"class": "m.T", "mcp": None}, True),
    (False, {"class": "m.T", "mcp": None}, False),
])
def test_the_manifest_entry_overrides_what_the_class_declares(tmp_path, declared, entry, expected):
    assert _register(tmp_path, _tool_class(declared), entry).mcp is expected


def test_a_plugin_tool_is_not_offered_over_mcp_by_default(tmp_path):
    assert BaseTool.mcp is False
    assert _register(tmp_path, _tool_class(False), {"class": "m.T", "modes": ["generation"]}).mcp is False


def test_the_manifest_schema_accepts_the_mcp_key_on_a_tool_entry():
    from src.platform.plugins.manifest import ChatToolSpec

    assert ChatToolSpec.model_validate({"class": "m.T", "mcp": True}).mcp is True
    assert ChatToolSpec.model_validate({"class": "m.T"}).mcp is None
