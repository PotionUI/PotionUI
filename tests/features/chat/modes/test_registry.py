"""Tests for the chat mode registry and builtin generation mode."""

import itertools

import pytest

from src.features.chat.exceptions import UnknownChatModeException
from src.features.chat.modes import (
    ChatMode,
    ChatModeRegistry,
    DuplicateChatModeError,
    DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE,
    GENERATION_MODE_ID,
    build_generation_mode,
)


def _mode(mode_id="test", source="builtin", **kwargs) -> ChatMode:
    return ChatMode(id=mode_id, name=mode_id.title(), source=source, **kwargs)


class TestChatModeRegistry:
    def test_register_and_get(self):
        registry = ChatModeRegistry()
        mode = _mode()
        registry.register(mode)
        assert registry.get("test") is mode

    def test_register_duplicate_raises(self):
        registry = ChatModeRegistry()
        registry.register(_mode())
        with pytest.raises(DuplicateChatModeError):
            registry.register(_mode())

    def test_get_missing_returns_none(self):
        registry = ChatModeRegistry()
        assert registry.get("nope") is None

    def test_get_all(self):
        registry = ChatModeRegistry()
        registry.register(_mode("a"))
        registry.register(_mode("b"))
        assert {m.id for m in registry.get_all()} == {"a", "b"}

    def test_require_returns_mode(self):
        registry = ChatModeRegistry()
        mode = _mode()
        registry.register(mode)
        assert registry.require("test") is mode

    def test_require_missing_raises(self):
        registry = ChatModeRegistry()
        with pytest.raises(UnknownChatModeException):
            registry.require("missing")

    def test_unregister(self):
        registry = ChatModeRegistry()
        registry.register(_mode())
        assert registry.unregister("test") is True
        assert registry.get("test") is None
        assert registry.unregister("test") is False

    def test_unregister_source(self):
        registry = ChatModeRegistry()
        registry.register(_mode("builtin-mode", source="builtin"))
        registry.register(_mode("plugin-mode-1", source="plugin-x"))
        registry.register(_mode("plugin-mode-2", source="plugin-x"))

        removed = registry.unregister_source("plugin-x")

        assert removed == 2
        assert registry.get("builtin-mode") is not None
        assert registry.get("plugin-mode-1") is None
        assert registry.get("plugin-mode-2") is None


class TestResolveSystemPrompt:
    # structured_reply=False isolates the base substitution behavior under
    # test from the reply-contract block appended by default — see
    # TestReplyContractBlock below for that behavior.

    def test_static_prompt_with_placeholder(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Tools:\n{{TOOL_HINTS}}\nEnd.", structured_reply=False)
        assert registry.resolve_system_prompt(mode, "- echo: hi") == "Tools:\n- echo: hi\nEnd."

    def test_static_prompt_without_placeholder(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Plain prompt.", structured_reply=False)
        assert registry.resolve_system_prompt(mode, "- echo: hi") == "Plain prompt."

    def test_empty_hints_substitutes_empty(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="A{{TOOL_HINTS}}B", structured_reply=False)
        assert registry.resolve_system_prompt(mode, "") == "AB"

    def test_callable_prompt_is_invoked_per_call(self):
        registry = ChatModeRegistry()
        values = iter(["first {{TOOL_HINTS}}", "second {{TOOL_HINTS}}"])
        mode = _mode(system_prompt=lambda: next(values), structured_reply=False)
        assert registry.resolve_system_prompt(mode, "X") == "first X"
        assert registry.resolve_system_prompt(mode, "Y") == "second Y"


class TestReplyContractBlock:
    def test_block_appears_when_flag_on(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Base.")
        prompt = registry.resolve_system_prompt(mode, "")
        assert "## improved" in prompt
        assert "## questions" in prompt

    def test_block_absent_when_flag_off(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Base.", structured_reply=False)
        prompt = registry.resolve_system_prompt(mode, "")
        assert prompt == "Base."

    def test_block_appended_after_tool_hints_substitution(self):
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Tools:\n{{TOOL_HINTS}}\nEnd.")
        prompt = registry.resolve_system_prompt(mode, "- echo: hi")
        assert prompt.startswith("Tools:\n- echo: hi\nEnd.")
        assert prompt.index("End.") < prompt.index("## improved")

    def test_block_instructs_resuming_after_a_quoted_answer(self):
        """A user turn quoting a docked question is the answer to it, not a
        fresh ask — the model must resume the task instead of just
        acknowledging and stopping (the "lost the loop" bug)."""
        registry = ChatModeRegistry()
        mode = _mode(system_prompt="Base.")
        prompt = registry.resolve_system_prompt(mode, "")
        assert "Resuming:" in prompt
        assert "continue the task immediately" in prompt


class TestBuildGenerationMode:
    def test_basic_shape(self):
        mode = build_generation_mode()
        assert mode.id == GENERATION_MODE_ID
        assert mode.source == "builtin"
        assert mode.default_route_prefixes  # frontend resolution anchors
        assert mode.resource_namespaces is None  # all namespaces visible

    def test_prompt_is_the_code_template(self):
        """The base prompt lives in code now, not in a setting."""
        mode = build_generation_mode()
        assert mode.system_prompt == DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE
        assert mode.resolve_prompt_template() == DEFAULT_TOOLS_SYSTEM_PROMPT_TEMPLATE

    def test_default_template_contains_instructions(self):
        registry = ChatModeRegistry()
        mode = build_generation_mode()
        prompt = registry.resolve_system_prompt(mode, "- echo: Use to echo.")
        assert "PotionUI" in prompt
        assert "- echo: Use to echo." in prompt
        assert "{{TOOL_HINTS}}" not in prompt
        assert "tool_action" in prompt
        assert "update_segment" in prompt

    def test_segment_update_is_taught_as_a_real_tool_call(self):
        """Prompt edits are taught as a real update_segment tool call now, not
        the <tool_action> markup a local model otherwise generalizes to any
        tool it has seen named (observed:
        `<tool_action type="update_video_director" operations=[...]>`)."""
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_generation_mode(), "- echo: Use to echo.")
        instruction = "call the update_segment tool"
        assert instruction in prompt
        nearby = prompt[prompt.index(instruction):prompt.index(instruction) + 200]
        assert "tool_action" not in nearby

    def test_director_segment_tag_is_scoped_and_complete(self):
        """The Video Director prompt-variant tag mirrors update_segment's contract:
        a complete example, with its scoping sentence sitting next to it, gated on
        get_video_director being allowed."""
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(
            build_generation_mode(), "- echo: Use to echo.", ["get_video_director"]
        )
        tag_example = (
            '<tool_action type="update_director_segment" segment_index="N" segment_id="ID">'
        )
        assert tag_example in prompt
        scoping = "ONLY for offering prompt versions"
        assert scoping in prompt
        assert 0 < prompt.index(scoping) - prompt.index(tag_example) < 400
        # Everything else about the document is user-only -- there is no tool
        # call, real or markup, for structural changes.
        assert "is user-only" in prompt

    def test_director_segment_tag_absent_without_get_video_director(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_generation_mode(), "- echo: Use to echo.", [])
        assert "update_director_segment" not in prompt
        assert "Video Director shots" not in prompt

    def test_global_direction_prompt_is_named_as_user_only(self):
        """The director paragraph must not leave "segment #N" as the only
        lever the model has heard of -- the shared Direction prompt is named
        explicitly as something the model has no tool for, same as
        durations/media/mode/shot count."""
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(
            build_generation_mode(), "- echo: Use to echo.", ["get_video_director"]
        )
        assert "shared Direction prompt" in prompt
        assert "is user-only" in prompt

    def test_global_direction_prompt_line_absent_without_get_video_director(self):
        registry = ChatModeRegistry()
        prompt = registry.resolve_system_prompt(build_generation_mode(), "- echo: Use to echo.", [])
        assert "shared Direction prompt" not in prompt


# Every tool name the builtin generation prompt references in a conditional block.
_PROMPT_TOOL_NAMES = [
    "get_form_state",
    "get_active_models",
    "search_model_prompts",
    "get_phrasebook_values",
    "enhance_prompt",
    "write_memory",
    "get_current_segments",
    "list_phrasebook_categories",
    "create_phrasebook_category",
    "create_phrasebook_values",
    "get_video_director",
]


class TestGenerationPromptIsSessionAccurate:
    """The assembled prompt must never name a tool outside the allowed set."""

    def _prompt(self, allowed):
        registry = ChatModeRegistry()
        mode = build_generation_mode()
        return registry.resolve_system_prompt(mode, "", list(allowed))

    @pytest.mark.parametrize(
        "allowed",
        [
            (),
            ("get_form_state",),
            ("get_active_models",),
            ("get_form_state", "get_active_models"),
            ("search_model_prompts",),
            ("write_memory",),
            ("get_current_segments",),
            ("list_phrasebook_categories",),
            ("get_phrasebook_values", "create_phrasebook_values"),
            ("get_video_director",),
            tuple(_PROMPT_TOOL_NAMES),
        ],
    )
    def test_no_tool_named_outside_allowed(self, allowed):
        prompt = self._prompt(allowed)
        for name in _PROMPT_TOOL_NAMES:
            if name not in allowed:
                assert name not in prompt, f"{name!r} leaked with allowed={allowed}"

    def test_property_over_many_subsets(self):
        """Sample subsets of the referenced tools; no disabled tool may appear."""
        core = _PROMPT_TOOL_NAMES[:5]  # keep the powerset small but representative
        for r in range(len(core) + 1):
            for allowed in itertools.combinations(core, r):
                prompt = self._prompt(allowed)
                for name in core:
                    if name not in allowed:
                        assert name not in prompt, f"{name!r} leaked with allowed={allowed}"

    def test_gather_rule_reduces_to_the_single_available_tool(self):
        prompt = self._prompt(["get_active_models"])
        assert "call get_active_models" in prompt
        assert "get_form_state" not in prompt

    def test_no_unresolved_markers_remain(self):
        prompt = self._prompt(["get_form_state", "write_memory"])
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
        assert "{{#ifany" not in prompt

    def test_no_unresolved_markers_remain_with_video_director(self):
        prompt = self._prompt(["get_current_segments", "get_video_director"])
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
        assert "{{#ifany" not in prompt
