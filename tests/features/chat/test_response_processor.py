"""Tests for ResponseProcessor."""

import pytest
from unittest.mock import Mock, MagicMock

from src.features.chat.response_processor import ResponseProcessor
from src.platform.plugins.hooks import HookContext, execute_hook
from src.features.chat.hooks import CHAT_RESPONSE_HOOKS


class TestRemoveThinkingTags:
    """Tests for _remove_thinking_tags method."""

    def setup_method(self):
        """Set up test fixtures."""
        self.processor = ResponseProcessor()

    def test_remove_think_tags(self):
        """Should remove <think> tags."""
        content = "Hello <think>internal reasoning</think> World"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Hello  World"

    def test_remove_thinking_tags(self):
        """Should remove <thinking> tags."""
        content = "Start <thinking>processing...</thinking> End"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Start  End"

    def test_remove_thought_tags(self):
        """Should remove <thought> tags."""
        content = "Begin <thought>contemplating</thought> Finish"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Begin  Finish"

    def test_remove_multiline_think_tags(self):
        """Should remove multiline thinking tags."""
        content = """Hello
<think>
This is some internal reasoning
that spans multiple lines
</think>
World"""
        result = self.processor._remove_thinking_tags(content)
        assert "reasoning" not in result
        assert "Hello" in result
        assert "World" in result

    def test_case_insensitive_removal(self):
        """Should remove tags regardless of case."""
        content = "Start <THINK>loud thinking</THINK> Middle <ThInKiNg>mixed</ThInKiNg> End"
        result = self.processor._remove_thinking_tags(content)
        assert "loud thinking" not in result
        assert "mixed" not in result
        assert "Start" in result
        assert "End" in result

    def test_think_tags_with_attributes(self):
        """Should remove think tags with attributes."""
        content = "Hello <think type='reasoning'>internal</think> World"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Hello  World"

    def test_multiple_think_tags(self):
        """Should remove multiple think tags."""
        content = "<think>first</think>Hello<think>second</think>World"
        result = self.processor._remove_thinking_tags(content)
        assert result == "HelloWorld"

    def test_collapses_excessive_whitespace(self):
        """Should collapse excessive newlines."""
        content = "Hello\n\n\n\n\nWorld"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Hello\n\nWorld"

    def test_strips_content(self):
        """Should strip leading/trailing whitespace."""
        content = "  \n  Hello World  \n  "
        result = self.processor._remove_thinking_tags(content)
        assert result == "Hello World"

    def test_no_tags_unchanged(self):
        """Should leave content without tags unchanged."""
        content = "Hello World"
        result = self.processor._remove_thinking_tags(content)
        assert result == "Hello World"

    def test_empty_string(self):
        """Should handle empty string."""
        result = self.processor._remove_thinking_tags("")
        assert result == ""


class TestProcessWithoutPlugins:
    """Tests for process method without plugin registry."""

    def setup_method(self):
        """Set up test fixtures."""
        self.processor = ResponseProcessor()

    def test_process_removes_thinking_tags(self):
        """Should remove thinking tags during processing."""
        content = "Hello <think>thinking</think> World"
        cleaned, parsed = self.processor.process(content)

        assert cleaned == "Hello  World"
        assert parsed == {'raw': 'Hello  World'}

    def test_process_returns_raw_content(self):
        """Should return raw cleaned content in parsed_content."""
        content = "Simple content"
        cleaned, parsed = self.processor.process(content)

        assert cleaned == "Simple content"
        assert parsed['raw'] == "Simple content"

    def test_process_with_mode(self):
        """Should accept mode parameter."""
        content = "Test content"
        cleaned, parsed = self.processor.process(content, mode='custom_type')

        assert cleaned == "Test content"
        assert parsed == {'raw': 'Test content'}


class TestProcessWithPlugins:
    """Tests for process method with plugin registry."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_registry = Mock()
        self.processor = ResponseProcessor(plugin_registry=self.mock_registry)

    def test_process_executes_hook(self):
        """Should execute CHAT_RESPONSE_TRANSFORM hook."""
        # Mock hook execution to return unchanged content
        mock_context = Mock()
        mock_context.data = {
            'content': 'Hello World',
            'mode': 'generation',
            'parsed_content': {'raw': 'Hello World'}
        }
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        content = "Hello World"
        cleaned, parsed = self.processor.process(content)

        # Verify hook was called
        self.mock_registry.execute_hook.assert_called_once()
        call_args = self.mock_registry.execute_hook.call_args
        assert call_args[0][0] == CHAT_RESPONSE_HOOKS.transform

    def test_process_uses_hook_modified_content(self):
        """Should use content modified by hook."""
        # Mock hook execution to modify content
        mock_context = Mock()
        mock_context.data = {
            'content': 'Modified by plugin',
            'mode': 'generation',
            'parsed_content': {'raw': 'Modified by plugin', 'custom': 'data'}
        }
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        content = "Original content"
        cleaned, parsed = self.processor.process(content)

        assert cleaned == 'Modified by plugin'
        assert parsed == {'raw': 'Modified by plugin', 'custom': 'data'}

    def test_process_passes_mode_to_hook(self):
        """Should pass mode to hook."""
        mock_context = Mock()
        mock_context.data = {
            'content': 'Content',
            'mode': 'custom_session',
            'parsed_content': {'raw': 'Content'}
        }
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        self.processor.process("Content", mode='custom_session')

        call_args = self.mock_registry.execute_hook.call_args
        initial_data = call_args[1]['initial_data']
        assert initial_data['mode'] == 'custom_session'

    def test_process_plugin_adds_actions(self):
        """Should support plugin adding actions to parsed_content."""
        # Simulate plugin detecting custom tag and adding action
        mock_context = Mock()
        mock_context.data = {
            'content': 'Jump now!',
            'mode': 'generation',
            'parsed_content': {
                'raw': 'Jump now!',
                'actions': ['jump']
            }
        }
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        content = "<action:jump> Jump now!"
        cleaned, parsed = self.processor.process(content)

        assert parsed.get('actions') == ['jump']


class TestReplyContractWiring:
    """Tests that ResponseProcessor splits the reply contract before hooks
    run, and never lets a hook clobber it."""

    def test_reply_contract_merged_without_plugins(self):
        processor = ResponseProcessor()
        content = "Done.\n## improved\n- tightened the wording\n"

        cleaned, parsed = processor.process(content)

        assert cleaned == "Done."
        assert parsed["reply_contract"] == {"improved": ["tightened the wording"]}
        assert parsed["raw"] == "Done."

    def test_no_reply_contract_key_when_nothing_parses(self):
        processor = ResponseProcessor()
        cleaned, parsed = processor.process("Just a plain reply.")

        assert cleaned == "Just a plain reply."
        assert "reply_contract" not in parsed

    def test_hook_sees_cleaned_prose_not_raw_sections(self):
        """The hook must receive content with the structured sections
        already stripped, not the raw reply."""
        mock_registry = Mock()
        mock_context = Mock()

        def _execute_hook(*args, **kwargs):
            initial_data = kwargs.get("initial_data") or args[1]
            mock_context.data = dict(initial_data)
            return mock_context, []

        mock_registry.execute_hook.side_effect = _execute_hook
        processor = ResponseProcessor(plugin_registry=mock_registry)

        content = "Lead line.\n## improved\n- change one\n"
        cleaned, parsed = processor.process(content)

        seen_by_hook = mock_registry.execute_hook.call_args[1]["initial_data"]["content"]
        assert seen_by_hook == "Lead line."
        assert cleaned == "Lead line."
        assert parsed["reply_contract"] == {"improved": ["change one"]}

    def test_hook_replacing_parsed_content_wholesale_does_not_drop_contract(self):
        """A hook that returns a brand-new parsed_content dict (rather than
        mutating the one it was given) must not silently lose the reply
        contract merged in before the hook ran."""
        mock_registry = Mock()
        mock_context = Mock()
        mock_context.data = {
            "content": "Lead line.",
            "mode": "generation",
            "parsed_content": {"raw": "Lead line.", "plugin_key": "plugin_value"},
        }
        mock_registry.execute_hook.return_value = (mock_context, [])
        processor = ResponseProcessor(plugin_registry=mock_registry)

        content = "Lead line.\n## questions\n1. more detail?\n"
        cleaned, parsed = processor.process(content)

        assert parsed["plugin_key"] == "plugin_value"
        assert parsed["reply_contract"] == {
            "questions": [{"text": "more detail?", "options": []}]
        }


class TestExecuteHook:
    """Tests for the shared execute_hook helper as used by ResponseProcessor."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_registry = Mock()
        self.processor = ResponseProcessor(plugin_registry=self.mock_registry)

    def test_execute_hook_returns_context_data(self):
        """Should return context data from hook execution."""
        mock_context = Mock()
        mock_context.data = {'key': 'value', 'blocked': False}
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(
            self.processor.plugins,
            CHAT_RESPONSE_HOOKS.transform,
            {'input': 'data'}
        )

        assert data == {'key': 'value', 'blocked': False}
        assert blocked is False

    def test_execute_hook_returns_blocked_status(self):
        """Should return blocked status from context."""
        mock_context = Mock()
        mock_context.data = {'blocked': True, 'block_reason': 'Test'}
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(
            self.processor.plugins,
            CHAT_RESPONSE_HOOKS.transform,
            {}
        )

        assert blocked is True

    def test_execute_hook_defaults_to_not_blocked(self):
        """Should default to not blocked if key missing."""
        mock_context = Mock()
        mock_context.data = {'some': 'data'}  # No 'blocked' key
        self.mock_registry.execute_hook.return_value = (mock_context, [])

        data, blocked = execute_hook(
            self.processor.plugins,
            CHAT_RESPONSE_HOOKS.transform,
            {}
        )

        assert blocked is False
