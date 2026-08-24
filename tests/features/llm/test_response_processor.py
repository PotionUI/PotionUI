"""Tests for LLMResponseProcessor."""

import pytest
from src.features.llm.response_processor import LLMResponseProcessor


class TestLLMResponseProcessor:
    """Tests for the LLMResponseProcessor class."""

    @pytest.fixture
    def processor(self):
        """Create a response processor instance."""
        return LLMResponseProcessor()

    # =========================================================================
    # Thinking Tag Removal Tests
    # =========================================================================

    def test_remove_thinking_tags_simple(self, processor):
        """Test removal of simple think tags."""
        content = "<think>Internal thought</think>Actual response"
        result = processor.remove_thinking_tags(content)
        assert result == "Actual response"
        assert "<think>" not in result

    def test_remove_thinking_tags_multiline(self, processor):
        """Test removal of multiline think tags."""
        content = """<think>
        I need to think about this.
        Let me consider the options.
        </think>

        Here is my response."""

        result = processor.remove_thinking_tags(content)
        assert "<think>" not in result
        assert "I need to think" not in result
        assert "Here is my response." in result

    def test_remove_thinking_tags_multiple_types(self, processor):
        """Test removal of different thinking tag types."""
        content = """<think>Think content</think>
        Response part 1.
        <thinking>Thinking content</thinking>
        Response part 2.
        <thought>Thought content</thought>
        Response part 3."""

        result = processor.remove_thinking_tags(content)
        assert "<think>" not in result
        assert "<thinking>" not in result
        assert "<thought>" not in result
        assert "Think content" not in result
        assert "Thinking content" not in result
        assert "Thought content" not in result
        assert "Response part 1." in result
        assert "Response part 2." in result
        assert "Response part 3." in result

    def test_remove_thinking_tags_case_insensitive(self, processor):
        """Test that tag removal is case insensitive."""
        content = "<THINK>Uppercase</THINK><Think>Mixed</Think>Response"
        result = processor.remove_thinking_tags(content)
        assert "Uppercase" not in result
        assert "Mixed" not in result
        assert "Response" in result

    def test_remove_thinking_tags_with_attributes(self, processor):
        """Test removal of tags with attributes."""
        content = '<think type="internal">Hidden</think>Visible'
        result = processor.remove_thinking_tags(content)
        assert "Hidden" not in result
        assert "Visible" in result

    def test_remove_thinking_tags_no_tags(self, processor):
        """Test that content without thinking tags is unchanged."""
        content = "This is a normal response without any thinking tags."
        result = processor.remove_thinking_tags(content)
        assert result == content

    def test_remove_thinking_tags_cleans_whitespace(self, processor):
        """Test that excessive whitespace is cleaned up."""
        content = """<think>Thought</think>


        Response with too much whitespace.


        More content."""

        result = processor.remove_thinking_tags(content)
        # Should not have triple newlines
        assert "\n\n\n" not in result
        assert "Response with too much whitespace." in result
        assert "More content." in result

    def test_remove_thinking_tags_complex_scenario(self, processor):
        """Test a complex real-world scenario."""
        content = """<think>
        I need to explain quantum physics in a simple way.
        Let me break this down into key concepts.
        </think>

        Quantum physics is the study of matter and energy at the smallest scales.

        <thinking>
        Should I mention wave-particle duality?
        Yes, that's fundamental.
        </thinking>

        Key concepts include:
        1. Wave-particle duality
        2. Quantum superposition

        <thought>Another internal thought</thought>

        3. Quantum entanglement"""

        expected_parts = [
            "Quantum physics is the study of matter and energy",
            "Key concepts include:",
            "1. Wave-particle duality",
            "2. Quantum superposition",
            "3. Quantum entanglement"
        ]

        result = processor.remove_thinking_tags(content)

        for part in expected_parts:
            assert part in result

        assert "<think>" not in result
        assert "<thinking>" not in result
        assert "<thought>" not in result
        assert "I need to explain" not in result
        assert "Should I mention" not in result
        assert "Another internal thought" not in result

    def test_remove_thinking_tags_empty_content(self, processor):
        """Test with empty content."""
        result = processor.remove_thinking_tags("")
        assert result == ""

    def test_remove_thinking_tags_only_thinking(self, processor):
        """Test content that is only thinking tags."""
        content = "<think>Only thoughts, no response</think>"
        result = processor.remove_thinking_tags(content)
        assert result == ""
