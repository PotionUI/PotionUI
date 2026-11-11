import inspect
from typing import Any, Dict, List

from src.features.llm.tools.builtin.utils import allowed_model_ids
from src.features.mcp import protocol
from src.features.mcp.catalog import CatalogEntry, build_catalog
from src.features.api_keys.settings import MCP_ENABLED_KEY

ACTS_AS_KEY_OWNER = (
    "An API key with the mcp scope lets an MCP client act as the key's owner. The tools it sees are "
    "the ones left on by three levels that can only narrow: the administrator's list, the owner's "
    "default and the key's own list. Tools that write or spend are off by default until the owner "
    "turns them on."
)


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

    def _document_tool(self, entry: CatalogEntry, rendered: Dict[str, Any]) -> Dict[str, Any]:
        mcp_shape = protocol._mcp_tool_schema(entry, rendered)
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
            "name": mcp_shape.get("name", entry.name),
            "description": mcp_shape.get("description", entry.tool.description),
            "group": entry.group,
            "mutating": entry.writes,
            "spends": entry.spends,
            "parameters": parameters,
        }

    def _governance(self) -> Dict[str, Any]:
        return {
            "acts_as_token_owner": ACTS_AS_KEY_OWNER,
            "global_setting_key": MCP_ENABLED_KEY,
            "global_default_enabled": False,
            "model_visibility_rule": _first_paragraph(allowed_model_ids.__doc__ or ""),
            "excluded_tool_names": sorted(
                entry.name for entry in build_catalog(self.tool_collaborators.tool_registry) if entry.unavailable_reason
            ),
        }

    def generate_documentation(self) -> Dict[str, Any]:
        candidates: List[CatalogEntry] = sorted(
            (
                entry for entry in build_catalog(self.tool_collaborators.tool_registry)
                if not entry.unavailable_reason
            ),
            key=lambda entry: entry.name,
        )
        schemas = self.tool_collaborators.tool_registry.get_schemas([entry.name for entry in candidates])
        schemas_by_name = {s.get("function", {}).get("name"): s for s in schemas}

        tools = [
            self._document_tool(entry, schemas_by_name[entry.name])
            for entry in candidates
            if entry.name in schemas_by_name
        ]

        return {
            "tools": tools,
            "total": len(tools),
            "governance": self._governance(),
        }
