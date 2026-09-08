"""MCP tool-surface documentation generator.

Reads the exact tool pool `tools/list` narrows and the exact MCP tool shape
`tools/list` returns (`src.features.mcp.protocol._candidate_tools` /
`_mcp_tool_schema`) rather than hand-listing tools, so this reference can
never drift from the JSON-RPC dispatcher's own exposure logic. Governance
facts (acting-as-owner, the two enable switches, model visibility) are
sourced from the same module's constants/docstrings, not invented prose.
"""
import inspect
from typing import Any, Dict, List

import src.features.mcp as mcp_package
from src.features.llm.tools.builtin.utils import allowed_model_ids
from src.features.mcp import protocol
from src.features.mcp.operations import MCP_ENABLED_KEY, MCP_USER_ENABLED_KEY


def _first_paragraph(doc: str) -> str:
    """First paragraph of a docstring, dedented and folded to one line."""
    if not doc:
        return ""
    first = inspect.cleandoc(doc).split("\n\n", 1)[0]
    return " ".join(line.strip() for line in first.splitlines()).strip()


class McpToolsDocumenter:
    """Generates documentation for the tool surface MCP exposes."""

    def __init__(self, tool_collaborators: protocol.McpToolCollaborators):
        self.tool_collaborators = tool_collaborators

    def _document_tool(self, tool: Any, rendered: Dict[str, Any]) -> Dict[str, Any]:
        mcp_shape = protocol._mcp_tool_schema(tool, rendered)
        input_schema = mcp_shape.get("inputSchema") or {}
        required = set(input_schema.get("required") or [])
        properties = input_schema.get("properties") or {}

        parameters = [
            {
                "name": param_name,
                "type": spec.get("type", "any") if isinstance(spec, dict) else "any",
                "required": param_name in required,
                "description": spec.get("description", "") if isinstance(spec, dict) else "",
            }
            for param_name, spec in properties.items()
        ]

        return {
            "name": mcp_shape.get("name", tool.name),
            "description": mcp_shape.get("description", tool.description),
            "group": tool.group,
            "mutating": tool.requires_approval,
            "parameters": parameters,
        }

    def _governance(self) -> Dict[str, Any]:
        return {
            "acts_as_token_owner": _first_paragraph(mcp_package.__doc__ or ""),
            "global_setting_key": MCP_ENABLED_KEY,
            "global_default_enabled": False,
            "user_setting_key": MCP_USER_ENABLED_KEY,
            "user_default_enabled": True,
            "model_visibility_rule": _first_paragraph(allowed_model_ids.__doc__ or ""),
            "excluded_tool_names": sorted(protocol.EXCLUDED_TOOL_NAMES),
        }

    def generate_documentation(self) -> Dict[str, Any]:
        candidates: List[Any] = sorted(
            protocol._candidate_tools(self.tool_collaborators), key=lambda t: t.name
        )
        schemas = self.tool_collaborators.tool_registry.get_schemas([t.name for t in candidates])
        schemas_by_name = {s.get("function", {}).get("name"): s for s in schemas}

        tools = [
            self._document_tool(tool, schemas_by_name[tool.name])
            for tool in candidates
            if tool.name in schemas_by_name
        ]

        return {
            "tools": tools,
            "total": len(tools),
            "governance": self._governance(),
        }
