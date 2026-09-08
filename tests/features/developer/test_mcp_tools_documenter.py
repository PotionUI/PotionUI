"""Tests for McpToolsDocumenter.

Uses the same scratch-database fixture MCP's own protocol tests use
(`tests.features.mcp.conftest.mcp_db`) so `ToolGovernanceRepository` has a
real migrated table to read from - the documenter must stay wired to the
actual `tools/list` code path, not a hand-rolled tool list.
"""
from unittest.mock import Mock

import pytest

from src.features.developer.mcp_tools_documenter import McpToolsDocumenter
from src.features.llm.tools.base import BaseTool, ToolResult
from src.features.llm.tools.governance_repository import ToolGovernanceRepository
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp import protocol
from src.features.mcp.protocol import McpToolCollaborators

from tests.features.mcp.conftest import mcp_db  # noqa: F401 -- reused fixture


class _SearchTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "search_things"

    @property
    def description(self):
        return "Searches for things."

    @property
    def group(self):
        return "Search"

    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search text."},
                "limit": {"type": "integer", "description": "Max results."},
            },
            "required": ["query"],
        }

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="ok")


class _MutatingTool(BaseTool):
    modes = ["generation"]

    @property
    def name(self):
        return "delete_thing"

    @property
    def description(self):
        return "Deletes a thing."

    @property
    def group(self):
        return "Danger"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    @property
    def requires_approval(self):
        return True

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="preview")

    async def execute_confirmed(self, context, **kwargs):
        return ToolResult(success=True, data="done")


class _ExcludedTool(BaseTool):
    """Named after a real MCP-excluded builtin (form-state dependent)."""
    modes = ["generation"]

    @property
    def name(self):
        return "get_active_models"

    @property
    def description(self):
        return "Reads live form state models."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="")


def _collaborators(governance_repo: ToolGovernanceRepository) -> McpToolCollaborators:
    registry = ToolRegistry()
    registry.register(_SearchTool())
    registry.register(_MutatingTool())
    registry.register(_ExcludedTool())
    return McpToolCollaborators(
        tool_registry=registry,
        tool_governance_repository=governance_repo,
        llm_repository=None,
    )


class TestMcpToolsDocumenter:
    def test_excluded_tool_is_absent(self, mcp_db):
        documenter = McpToolsDocumenter(_collaborators(ToolGovernanceRepository()))

        result = documenter.generate_documentation()

        names = {t["name"] for t in result["tools"]}
        assert names == {"search_things", "delete_thing"}
        assert result["total"] == 2

    def test_parameters_carry_required_flag_type_and_description(self, mcp_db):
        documenter = McpToolsDocumenter(_collaborators(ToolGovernanceRepository()))

        result = documenter.generate_documentation()

        search = next(t for t in result["tools"] if t["name"] == "search_things")
        params_by_name = {p["name"]: p for p in search["parameters"]}

        assert params_by_name["query"]["required"] is True
        assert params_by_name["query"]["type"] == "string"
        assert params_by_name["query"]["description"] == "The search text."
        assert params_by_name["limit"]["required"] is False
        assert params_by_name["limit"]["type"] == "integer"

    def test_mutating_flag_reflects_requires_approval(self, mcp_db):
        documenter = McpToolsDocumenter(_collaborators(ToolGovernanceRepository()))

        result = documenter.generate_documentation()
        by_name = {t["name"]: t for t in result["tools"]}

        assert by_name["delete_thing"]["mutating"] is True
        assert by_name["search_things"]["mutating"] is False

    def test_governance_facts_are_sourced_from_the_protocol_and_operations_modules(self, mcp_db):
        documenter = McpToolsDocumenter(_collaborators(ToolGovernanceRepository()))

        governance = documenter.generate_documentation()["governance"]

        assert governance["global_setting_key"] == "mcp_enabled"
        assert governance["global_default_enabled"] is False
        assert governance["user_setting_key"] == "mcp_user_enabled"
        assert governance["user_default_enabled"] is True
        assert "get_active_models" in governance["excluded_tool_names"]
        assert governance["acts_as_token_owner"]
        assert governance["model_visibility_rule"]

    def test_tool_set_matches_the_real_mcp_dispatcher(self, mcp_db):
        """The documenter must read the exact code path `tools/list` uses -
        this pins that there is no drift between the reference page and the
        protocol's own tool exposure."""
        collaborators = _collaborators(ToolGovernanceRepository())
        documenter = McpToolsDocumenter(collaborators)

        documented_names = {t["name"] for t in documenter.generate_documentation()["tools"]}
        dispatcher_names = {
            t["name"] for t in protocol.list_tools(collaborators, user_id="user-1")["tools"]
        }

        assert documented_names == dispatcher_names
        assert documented_names  # not vacuously equal
