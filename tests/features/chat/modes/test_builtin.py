"""Tests for the generation mode's code-owned system prompt template."""

from src.features.chat.modes import ChatModeRegistry, build_generation_mode


class TestMemoryPromptBullet:
    def _resolve(self, allowed):
        registry = ChatModeRegistry()
        mode = build_generation_mode()
        return registry.resolve_system_prompt(mode, "- some_tool: hint", allowed)

    def test_update_over_append_instruction_present_when_both_tools_allowed(self):
        prompt = self._resolve(["write_memory", "update_memory"])
        assert "Save the pattern, not the instance" in prompt
        assert "call update_memory by its scope and key" in prompt
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt

    def test_update_over_append_instruction_absent_without_update_memory(self):
        prompt = self._resolve(["write_memory"])
        assert "Save the pattern, not the instance" in prompt
        assert "call update_memory by its scope and key" not in prompt
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt

    def test_memory_bullet_absent_without_write_memory(self):
        prompt = self._resolve(["update_memory"])
        assert "Save the pattern, not the instance" not in prompt
        assert "call update_memory by its scope and key" not in prompt
        assert "{{#if" not in prompt
        assert "{{/if" not in prompt
