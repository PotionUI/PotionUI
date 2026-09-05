"""A live snapshot of real chat-tool schemas for evaluation fixtures.

Scenario fixtures must never hand-copy a tool schema — they reference a tool
by name and this module resolves the actual JSON schema from the same
registration path the composition root uses (``register_builtin_tools``,
``src/bootstrap/container.py``), so a schema change in the tool itself is
picked up automatically instead of silently drifting from a stale copy.

The ComfyUI import wizard's tools (``propose_form_changes``,
``get_workflow_inputs``) are not builtin; they are added here by importing
the plugin's tool module directly and registering the same classes the
plugin manifest points at (``backend.chat.tools:ProposeFormChangesTool`` /
``:GetWorkflowInputsTool`` in
``content/plugins/marketplace/comfyui-backend/manifest.yml``), the way the
plugin loader would resolve them, but without going through plugin
discovery/manifest machinery — this stays a fast, offline fixture snapshot,
not a plugin-loading test.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Dict, List, Optional

from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry

_COMFYUI_PLUGIN_DIR = (
    Path(__file__).resolve().parents[3]
    / "content" / "plugins" / "marketplace" / "comfyui-backend"
)


def _register_comfyui_import_tools(registry: ToolRegistry) -> None:
    plugin_dir = str(_COMFYUI_PLUGIN_DIR)
    if not _COMFYUI_PLUGIN_DIR.is_dir():
        return
    added = plugin_dir not in sys.path
    if added:
        sys.path.insert(0, plugin_dir)
    try:
        module = importlib.import_module("backend.chat.tools")
        registry.register(module.GetWorkflowInputsTool(), source="comfyui-backend")
        registry.register(module.ProposeFormChangesTool(), source="comfyui-backend")
    finally:
        if added and plugin_dir in sys.path:
            sys.path.remove(plugin_dir)


def build_registry() -> ToolRegistry:
    """The real tool registry, builtin tools plus the ComfyUI import tools."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    _register_comfyui_import_tools(registry)
    return registry


def schemas_by_name(
    registry: Optional[ToolRegistry] = None,
    names: Optional[List[str]] = None,
) -> Dict[str, Dict]:
    """``{tool_name: schema}`` for the given registry (a fresh one by default)."""
    registry = registry if registry is not None else build_registry()
    return {schema["function"]["name"]: schema for schema in registry.get_schemas(names)}
