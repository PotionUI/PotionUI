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
