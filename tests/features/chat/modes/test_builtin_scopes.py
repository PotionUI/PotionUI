"""Tests for the page-bound scope modes (history/models/phrasebook/prompts)."""

import pytest

from src.features.chat.modes import (
    ChatModeRegistry,
    ELIXIRS_MODE_ID,
    GENERATION_MODE_ID,
    HISTORY_MODE_ID,
    MODELS_MODE_ID,
    PHRASEBOOK_MODE_ID,
    PROMPTS_MODE_ID,
    build_elixirs_mode,
    build_generation_mode,
    build_history_mode,
    build_models_mode,
    build_phrasebook_mode,
    build_prompts_mode,
)
from src.features.chat.context_builder import ChatContextBuilder
from src.features.chat.reply_contract import (
    ACTION_CONTINUATION_NUDGE,
    REPLY_CONTRACT_PROMPT_BLOCK,
    REPLY_CONTRACT_REMINDER,
    TOOL_LOOP_CONTINUATION_NUDGE,
    continuation_nudge,
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

    def test_phrasebook_prompt_keeps_the_look_before_you_change_rule(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_phrasebook_mode(), "", ["get_phrasebook_values"])
        assert "{{#if" not in prompt
        assert "Look up what exists before you change it" in prompt

    def test_prompts_prompt_keeps_the_verbatim_rule_only_with_a_save_tool(self):
        registry = ChatModeRegistry()
        with_save = registry.resolve_system_prompt(
            build_prompts_mode(), "", ["search_model_prompts", "add_prompt", "edit_prompt", "delete_prompt"],
        )
        without_save = registry.resolve_system_prompt(build_prompts_mode(), "", ["search_model_prompts"])
        assert "{{#if" not in with_save
        assert "save it exactly as written" in with_save
        assert "Compose or improve a prompt only when asked" in with_save
        assert "exactly as written" not in without_save


class TestPromptWritingContractByMode:
    @pytest.mark.parametrize("builder", [build_history_mode, build_models_mode, build_phrasebook_mode])
    def test_modes_that_do_not_write_prompt_text_drop_the_contract(self, builder):
        mode = builder()
        assert mode.structured_reply is False
        prompt = ChatModeRegistry().resolve_system_prompt(mode, "", [])
        assert REPLY_CONTRACT_PROMPT_BLOCK not in prompt
        history = [{"role": "user", "content": "hi"}]
        ChatContextBuilder.inject_reply_contract_reminder_block(history, mode)
        assert REPLY_CONTRACT_REMINDER not in history[-1]["content"]
        assert continuation_nudge(mode.structured_reply) == ACTION_CONTINUATION_NUDGE

    def test_prompts_mode_keeps_the_contract_because_it_writes_prompt_text(self):
        mode = build_prompts_mode()
        assert mode.structured_reply is True
        prompt = ChatModeRegistry().resolve_system_prompt(mode, "", [])
        assert REPLY_CONTRACT_PROMPT_BLOCK in prompt
        assert continuation_nudge(mode.structured_reply) == TOOL_LOOP_CONTINUATION_NUDGE


class TestHistoryModeReplyContract:
    def test_history_prompt_has_no_prompt_writing_contract(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_history_mode(), "", ["organize_gallery"])
        assert REPLY_CONTRACT_PROMPT_BLOCK not in prompt
        assert "## improved" not in prompt

    def test_history_keeps_the_action_nudge_without_prompt_writing_wording(self):
        nudge = continuation_nudge(build_history_mode().structured_reply)
        assert nudge == ACTION_CONTINUATION_NUDGE
        assert "call the next tool NOW" in nudge
        assert "tool_action" not in nudge
        assert "prompt" not in nudge

    def test_generation_keeps_the_full_nudge(self):
        assert continuation_nudge(build_generation_mode().structured_reply) == TOOL_LOOP_CONTINUATION_NUDGE

    def test_generation_prompt_keeps_the_contract(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_generation_mode(), "", [])
        assert REPLY_CONTRACT_PROMPT_BLOCK in prompt

    def test_history_send_gets_no_reply_contract_reminder(self):
        history = [{"role": "user", "content": "hi"}]
        ChatContextBuilder.inject_reply_contract_reminder_block(history, build_history_mode())
        assert REPLY_CONTRACT_REMINDER not in history[-1]["content"]


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

    def test_models_mode_does_not_see_history_mode_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_models_mode())}
        assert "organize_gallery" not in names

    def test_phrasebook_mode_sees_all_six_phrasebook_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_phrasebook_mode())}
        assert names >= {
            "list_phrasebook_categories",
            "get_phrasebook_values",
            "create_phrasebook_category",
            "create_phrasebook_values",
            "remove_phrasebook_values",
            "update_phrasebook_values",
        }

    def test_prompts_mode_sees_search_and_crud_tools(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_prompts_mode())}
        assert names >= {"search_model_prompts", "add_prompt", "edit_prompt", "delete_prompt"}

    def test_generation_mode_keeps_the_widened_tools_it_still_owns(self):
        registry = self._registry()
        names = {t.name for t in registry.get_for_mode(build_generation_mode())}
        assert {"list_models", "get_model_info", "search_model_prompts", "list_phrasebook_categories"} <= names

    def test_page_owned_tools_left_the_generation_mode(self):
        registry = self._registry()
        generation = {t.name for t in registry.get_for_mode(build_generation_mode())}
        history = {t.name for t in registry.get_for_mode(build_history_mode())}
        prompts = {t.name for t in registry.get_for_mode(build_prompts_mode())}
        assert not generation & {"add_prompt", "edit_prompt", "delete_prompt", "manage_collections", "organize_gallery"}
        assert {"manage_collections", "organize_gallery"} <= history
        assert {"add_prompt", "edit_prompt", "delete_prompt"} <= prompts

    def test_global_tools_appear_in_every_scope_mode(self):
        """Memory tools (modes=None) are global and must appear everywhere,
        including the new page-bound scopes."""
        registry = self._registry()
        for builder in _BUILDERS.values():
            names = {t.name for t in registry.get_for_mode(builder())}
            assert "write_memory" in names


class TestElixirsMode:
    def test_it_is_bound_by_the_elixir_tab_not_by_a_route(self):
        mode = build_elixirs_mode()
        assert mode.id == ELIXIRS_MODE_ID == "elixirs"
        assert mode.default_route_prefixes == []
        assert mode.icon == "flask"
        assert mode.structured_reply is False

    def test_the_prompt_says_it_edits_and_never_brews(self):
        prompt = ChatModeRegistry().resolve_system_prompt(
            build_elixirs_mode(), "", ["get_elixir", "edit_elixir", "get_step_settings", "get_choice_outputs", "choose_outputs"]
        )
        assert "{{#if" not in prompt and "{{#ifany" not in prompt
        assert "PotionAI" in prompt and "Potion AI" not in prompt
        assert "Brew" in prompt and "Continue" in prompt
        assert "Call get_elixir first" in prompt

    def test_tool_lines_drop_without_their_tools(self):
        prompt = ChatModeRegistry().resolve_system_prompt(build_elixirs_mode(), "", [])
        assert "get_elixir" not in prompt and "choose_outputs" not in prompt

    def test_the_mode_sees_the_elixir_tools_and_no_generation_runner(self):
        registry = ToolRegistry()
        register_builtin_tools(registry)
        names = {t.name for t in registry.get_for_mode(build_elixirs_mode())}
        assert {"get_elixir", "get_step_settings", "get_choice_outputs", "edit_elixir", "choose_outputs", "get_preset_info"} <= names
        assert not names & {"update_form_settings", "run_generation", "start_generation", "run_media_tool"}
