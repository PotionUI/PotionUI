"""Tests for prompt-database DTO validation."""
import pytest
from pydantic import ValidationError

from src.features.prompt_database.dto import PromptRequest
from src.features.segments.dto import RichSegment


class TestPromptRequestSegments:
    def test_rejects_empty_segments(self):
        with pytest.raises(ValidationError):
            PromptRequest(segments=[])

    def test_accepts_at_least_one_segment(self):
        req = PromptRequest(segments=[RichSegment(content="a fox", name="Subject")])
        assert len(req.segments) == 1


class TestPromptRequestVariables:
    def test_defaults_to_none(self):
        req = PromptRequest(segments=[RichSegment(content="a fox")])
        assert req.variables is None

    def test_accepts_a_valid_text_and_choice_map(self):
        req = PromptRequest(
            segments=[RichSegment(content="a fox")],
            variables={
                "mood": {"type": "text", "value": "noir"},
                "scene": {
                    "type": "choice",
                    "mode": "shuffle",
                    "pinnedIndex": None,
                    "options": ["day", "night"],
                },
            },
        )
        assert req.variables["mood"]["value"] == "noir"

    def test_rejects_an_invalid_name(self):
        with pytest.raises(ValidationError, match="not a valid variable name"):
            PromptRequest(
                segments=[RichSegment(content="a fox")],
                variables={"1bad": {"type": "text", "value": "x"}},
            )

    def test_rejects_an_unknown_definition_shape(self):
        with pytest.raises(ValidationError, match="Use text or choice"):
            PromptRequest(
                segments=[RichSegment(content="a fox")],
                variables={"mood": {"type": "number", "value": 1}},
            )

    def test_rejects_a_condition_cycle(self):
        with pytest.raises(ValidationError, match="cycle"):
            PromptRequest(
                segments=[RichSegment(content="a fox")],
                variables={
                    "a": {
                        "type": "choice", "mode": "shuffle", "pinnedIndex": None,
                        "options": [{"text": "x", "when": {"var": "b", "values": ["y"]}}],
                    },
                    "b": {
                        "type": "choice", "mode": "shuffle", "pinnedIndex": None,
                        "options": [{"text": "y", "when": {"var": "a", "values": ["x"]}}],
                    },
                },
            )
