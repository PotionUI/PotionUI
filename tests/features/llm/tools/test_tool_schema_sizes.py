"""Every builtin tool's schema is reshipped on every iteration of every chat
turn -- a regression guard against a tool's description growing unbounded."""

import json

from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry

# The worst offender outside the director tools (manage_collections, ~2.9k)
# stays well under this; this is a general ceiling for any builtin tool.
_MAX_SCHEMA_CHARS = 4000
# Tighter than the general ceiling so a regression on this one specifically
# (it was 4988 chars before its description was trimmed) is caught even if
# it stays under the general 4000 cap.
_MAX_MUSIC_DIRECTOR_UPDATE_SCHEMA_CHARS = 3450


def _registered_tools():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry.get_all()


def test_no_builtin_tool_schema_exceeds_the_general_ceiling():
    oversized = [
        (tool.name, len(json.dumps(tool.to_schema())))
        for tool in _registered_tools()
        if len(json.dumps(tool.to_schema())) > _MAX_SCHEMA_CHARS
    ]
    assert not oversized, f"tool schemas over {_MAX_SCHEMA_CHARS} chars: {oversized}"


def test_update_music_director_schema_stays_under_its_tighter_target():
    tool = next(t for t in _registered_tools() if t.name == "update_music_director")
    size = len(json.dumps(tool.to_schema()))
    assert size <= _MAX_MUSIC_DIRECTOR_UPDATE_SCHEMA_CHARS, size
