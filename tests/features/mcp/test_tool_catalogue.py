import importlib.util
from pathlib import Path

import pytest

from src.features.llm.tools.base import MCP_NEEDS_LIVE_FORM, MCP_USE_START_GENERATION, BaseTool, ToolResult
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry
from src.features.mcp.catalog import build_catalog

SPENDS = {"enhance_prompt", "start_generation", "run_media_tool"}

READS = {
    "list_segment_categories", "get_saved_segments", "get_segment_templates",
    "get_model_info", "get_preset_info", "list_presets", "list_models", "search_models",
    "list_phrasebook_categories", "get_phrasebook_values",
    "enhance_prompt", "search_model_prompts", "search_gallery", "get_prompt", "list_prompts",
    "list_library_items", "read_memory", "get_prompt_variables", "list_tab_drafts", "write_condition",
}

WRITES = {
    "create_segment_category", "update_segment_category", "delete_segment_category",
    "create_saved_segment", "update_saved_segment", "delete_saved_segment",
    "create_segment_template", "update_segment_template", "delete_segment_template",
    "create_phrasebook_category", "update_phrasebook_category", "delete_phrasebook_category",
    "create_phrasebook_values", "remove_phrasebook_values", "update_phrasebook_values",
    "add_prompt", "edit_prompt", "delete_prompt",
    "write_memory", "update_memory", "delete_memory",
    "manage_collections", "organize_gallery", "start_generation", "run_media_tool",
    "manage_prompt_variables",
}

CANNOT_RUN = {
    "get_form_state": (False, MCP_NEEDS_LIVE_FORM),
    "update_form_settings": (True, MCP_NEEDS_LIVE_FORM),
    "get_current_segments": (False, MCP_NEEDS_LIVE_FORM),
    "get_video_director": (False, MCP_NEEDS_LIVE_FORM),
    "get_music_director": (False, MCP_NEEDS_LIVE_FORM),
    "update_music_director": (True, MCP_NEEDS_LIVE_FORM),
    "get_active_models": (False, MCP_NEEDS_LIVE_FORM),
    "set_prompt_relay_timeline": (True, MCP_NEEDS_LIVE_FORM),
    "set_regional_prompt": (True, MCP_NEEDS_LIVE_FORM),
    "run_generation": (True, MCP_USE_START_GENERATION),
}

NOT_IN_THE_CATALOGUE = {"get_elixir", "get_step_settings", "get_choice_outputs", "edit_elixir", "choose_outputs"}

EXPECTED = (
    {name: (False, name in SPENDS, None) for name in READS}
    | {name: (True, name in SPENDS, None) for name in WRITES}
    | {name: (writes, False, reason) for name, (writes, reason) in CANNOT_RUN.items()}
)


@pytest.fixture(scope="module")
def registry():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def test_the_catalogue_pins_every_tool_its_writes_and_spends_flags_and_its_reason(registry):
    actual = {entry.name: (entry.writes, entry.spends, entry.unavailable_reason) for entry in build_catalog(registry)}

    assert actual == EXPECTED


def test_the_catalogue_is_the_registry_without_the_elixir_tools(registry):
    names = {tool.name for tool in registry.get_all()}

    assert names - {entry.name for entry in build_catalog(registry)} == NOT_IN_THE_CATALOGUE


def test_start_generation_is_the_only_generation_tool_over_mcp(registry):
    offered = {entry.name for entry in build_catalog(registry) if not entry.unavailable_reason}

    assert "start_generation" in offered
    assert "run_generation" not in offered
    assert "list_models" in offered


def test_every_offered_tool_has_a_description_for_the_screen(registry):
    for entry in build_catalog(registry):
        assert entry.description.strip(), entry.name
        assert entry.group.strip(), entry.name


def test_the_tools_the_migration_keeps_on_are_exactly_those_that_write_or_spend(registry):
    path = Path(__file__).resolve().parents[3] / "src" / "platform" / "database" / "migrations" / "096_mcp_tool_seed.py"
    spec = importlib.util.spec_from_file_location("seed_literal", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    offered = [entry for entry in build_catalog(registry) if not entry.unavailable_reason]

    assert set(module._KEPT_ON) == {entry.name for entry in offered if entry.writes or entry.spends}
    assert set(module._WRITE_TOOLS) == {entry.name for entry in offered if entry.writes}
    assert set(module._READ_TOOLS) == {entry.name for entry in offered if not entry.writes and not entry.spends} - {"list_models"}


class _Tool(BaseTool):
    mcp = True

    @property
    def description(self):
        return "d"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, context, **kwargs):
        return ToolResult(success=True, data="")


def _named(tool_name, **attributes):
    tool_type = type("Tool", (_Tool,), {"name": tool_name, **attributes})
    return tool_type()


def test_a_tool_that_says_nothing_about_spending_does_not_spend():
    assert build_catalog_of(_named("quiet"))[0].spends is False


def test_a_tool_that_declares_it_spends_says_so_in_the_catalogue():
    assert build_catalog_of(_named("metered", spends=True))[0].spends is True


def test_a_tool_that_says_nothing_about_writing_counts_as_writing():
    tool = _named("silent")

    assert tool.writes is True
    assert build_catalog_of(tool)[0].writes is True


def test_a_tool_is_not_offered_unless_it_declares_mcp():
    undeclared = _named("undeclared", mcp=False)

    assert build_catalog_of(undeclared) == []


def test_a_tool_that_declares_a_reason_is_in_the_catalogue_even_without_mcp():
    blocked = _named("blocked", mcp=False, mcp_unavailable="not here")

    [entry] = build_catalog_of(blocked)

    assert entry.unavailable_reason == "not here"


def test_a_tool_not_available_without_a_form_is_left_out():
    class _NeedsForm(_Tool):
        name = "needs_form"

        def is_available(self, form_state):
            return form_state is not None

    assert build_catalog_of(_NeedsForm()) == []


def build_catalog_of(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return build_catalog(registry)
