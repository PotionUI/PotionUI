import json
import re

from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from src.features.llm.tools.tool_conditionals import render_tool_conditionals
from tests.evaluation.chat.tool_snapshot import build_registry

_MAX_SCHEMA_CHARS = 2000

_SCHEMA_CHAR_ALLOWANCES = {
    "manage_prompt_variables": 3425,
    "update_music_director": 3450,
    "manage_collections": 3100,
    "organize_gallery": 2500,
    "start_generation": 2350,
    "add_prompt": 2200,
    "run_media_tool": 5324,
}

_MAX_HINT_SHINGLE_SHARE = 0.40


def _builtin_tools():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry.get_all()


def _shingles(text, size=5):
    words = re.findall(r"[a-z0-9_']+", text.lower())
    return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}


def test_no_builtin_tool_declares_a_hint():
    hinted = [tool.name for tool in _builtin_tools() if tool.hint]
    assert not hinted, f"builtin tools put their guidance in description, not hint: {hinted}"


def test_builtin_schemas_stay_under_the_cap_unless_allowed():
    oversized = []
    for tool in _builtin_tools():
        size = len(json.dumps(tool.to_schema()))
        cap = _SCHEMA_CHAR_ALLOWANCES.get(tool.name, _MAX_SCHEMA_CHARS)
        if size > cap:
            oversized.append((tool.name, size, cap))
    assert not oversized, f"tool schemas over their cap (name, chars, cap): {oversized}"


def test_every_allowance_is_still_needed():
    sizes = {tool.name: len(json.dumps(tool.to_schema())) for tool in _builtin_tools()}
    stale = [name for name in _SCHEMA_CHAR_ALLOWANCES if sizes.get(name, 0) <= _MAX_SCHEMA_CHARS]
    assert not stale, f"drop these from the allow-list, they fit the general cap: {stale}"


def test_plugin_hints_do_not_restate_their_description():
    registry = build_registry()
    names = [tool.name for tool in registry.get_all()]
    restated = []
    for tool in registry.get_all():
        if not tool.hint:
            continue
        hint = _shingles(render_tool_conditionals(tool.hint, names))
        description = _shingles(render_tool_conditionals(tool.description, names))
        if hint and len(hint & description) / len(hint) > _MAX_HINT_SHINGLE_SHARE:
            restated.append(tool.name)
    assert not restated, f"hints that repeat their schema description: {restated}"
