"""Tests for the page-bound scope modes (history/models/phrasebook/prompts)."""

import pytest

from src.features.chat.modes import (
    ChatModeRegistry,
    GENERATION_MODE_ID,
    HISTORY_MODE_ID,
    MODELS_MODE_ID,
    PHRASEBOOK_MODE_ID,
    PROMPTS_MODE_ID,
    build_generation_mode,
    build_history_mode,
    build_models_mode,
    build_phrasebook_mode,
    build_prompts_mode,
)
from src.features.llm.tools.builtin import register_builtin_tools
from src.features.llm.tools.registry import ToolRegistry

_BUILDERS = {
    HISTORY_MODE_ID: build_history_mode,
    MODELS_MODE_ID: build_models_mode,
    PHRASEBOOK_MODE_ID: build_phrasebook_mode,
    PROMPTS_MODE_ID: build_prompts_mode,
}

_EXPECTED_PREFIXES = {
    HISTORY_MODE_ID: ["/history"],
    MODELS_MODE_ID: ["/models"],
    PHRASEBOOK_MODE_ID: ["/phrasebook"],
    PROMPTS_MODE_ID: ["/prompts"],
}

_EXPECTED_ICONS = {
    HISTORY_MODE_ID: "clock",
    MODELS_MODE_ID: "database",
    PHRASEBOOK_MODE_ID: "hash",
    PROMPTS_MODE_ID: "book",
}


class TestScopeModeShape:
    @pytest.mark.parametrize("mode_id", list(_BUILDERS))
    def test_basic_shape(self, mode_id):
        mode = _BUILDERS[mode_id]()
        assert mode.id == mode_id
        assert mode.source == "builtin"
        assert mode.default_route_prefixes == _EXPECTED_PREFIXES[mode_id]
        assert mode.icon == _EXPECTED_ICONS[mode_id]
        assert mode.name
        assert mode.description

    @pytest.mark.parametrize("mode_id", list(_BUILDERS))
    def test_prompt_resolves_without_unresolved_markers_with_no_tools_allowed(self, mode_id):
        registry = ChatModeRegistry()
        mode = _BUILDERS[mode_id]()
        prompt = registry.resolve_system_prompt(mode, "- some_tool: hint", [])
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
        assert "{{#ifany" not in prompt

    def test_phrasebook_prompt_joins_tool_names_with_all_tools_allowed(self):
        """Regression: the nested {{#ifany}} separators in the phrasebook and
        prompts templates were once closed with {{/if}} instead of {{/ifany}},
        which desynced the block matcher and left raw markers in the prompt."""
        registry = ChatModeRegistry()
        allowed = [
            "list_phrasebook_categories", "list_phrasebook_values", "get_phrasebook_values",
            "create_phrasebook_category", "create_phrasebook_values",
            "update_phrasebook_values", "remove_phrasebook_values",
        ]
        prompt = registry.resolve_system_prompt(build_phrasebook_mode(), "", allowed)
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
        assert "list_phrasebook_categories, list_phrasebook_values and get_phrasebook_values" in prompt
        assert "create_phrasebook_category, create_phrasebook_values, update_phrasebook_values and remove_phrasebook_values" in prompt

    def test_prompts_prompt_joins_tool_names_with_all_tools_allowed(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(
            build_prompts_mode(), "", ["search_model_prompts", "add_prompt", "edit_prompt", "delete_prompt"],
        )
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
        assert "add_prompt, edit_prompt and delete_prompt" in prompt


class TestScopeModeRegistration:
    def test_all_five_builtin_modes_register_without_collision(self):
        registry = ChatModeRegistry()
        registry.register(build_generation_mode())
        for builder in _BUILDERS.values():
            registry.register(builder())
        ids = {m.id for m in registry.get_all()}
        assert ids == {GENERATION_MODE_ID, *_BUILDERS.keys()}

    @pytest.mark.parametrize("mode_id", list(_BUILDERS))
    def test_each_mode_resolvable_by_id(self, mode_id):
        registry = ChatModeRegistry()
        registry.register(_BUILDERS[mode_id]())
        assert registry.require(mode_id).id == mode_id


class TestScopeModeToolMembership:
    """`ToolRegistry.get_for_mode` must scope real, registered tools correctly:
    a tool widened to a scope mode appears there, and a tool that was never
    widened to that mode does not leak in just because it's registered."""

    def _registry(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        return registry

    def test_history_mode_sees_organize_gallery_and_manage_collections(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_history_mode())}
        assert "organize_gallery" in names
        assert "manage_collections" in names

    def test_history_mode_does_not_see_phrasebook_or_model_mutation_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_history_mode())}
        assert "create_phrasebook_values" not in names
        assert "list_models" not in names

    def test_models_mode_sees_list_models_and_get_model_info(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_models_mode())}
        assert "list_models" in names
        assert "get_model_info" in names

    def test_models_mode_does_not_see_history_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_models_mode())}
        assert "organize_gallery" not in names

    def test_phrasebook_mode_sees_all_seven_phrasebook_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_phrasebook_mode())}
        assert names >= {
            "list_phrasebook_categories",
            "get_phrasebook_values",
            "list_phrasebook_values",
            "create_phrasebook_category",
            "create_phrasebook_values",
            "remove_phrasebook_values",
            "update_phrasebook_values",
        }

    def test_prompts_mode_sees_search_and_crud_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_prompts_mode())}
        assert names >= {"search_model_prompts", "add_prompt", "edit_prompt", "delete_prompt"}

    def test_generation_mode_still_sees_every_widened_tool(self):
        """Widening a tool's `modes` list to a new scope must not drop it from
        the generation mode it originally belonged to."""
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_generation_mode())}
        assert "organize_gallery" in names
        assert "manage_collections" in names
        assert "list_models" in names
        assert "get_model_info" in names
        assert "search_model_prompts" in names
        assert "add_prompt" in names
        assert "list_phrasebook_categories" in names

    def test_global_tools_appear_in_every_scope_mode(self):
        """Memory tools (modes=None) are global and must appear everywhere,
        including the new page-bound scopes."""
        registry = self._registry()
        for builder in _BUILDERS.values():
            names = {t.name for t in registry.get_for_mode(builder())}
            assert "write_memory" in names
