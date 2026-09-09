"""Tests for segments DTO validation (required-field policy)."""
import pytest
from pydantic import ValidationError

from src.features.segments.dto import (
    RichSegment,
    SavedSegmentRequest,
    SegmentCategoryRequest,
    SegmentTemplateRequest,
)


class TestSegmentCategoryRequestName:
    @pytest.mark.parametrize("name", ["", "   "])
    def test_rejects_blank_name(self, name):
        with pytest.raises(ValidationError):
            SegmentCategoryRequest(name=name)

    def test_strips_and_accepts_name(self):
        req = SegmentCategoryRequest(name="  Style  ")
        assert req.name == "Style"


class TestSavedSegmentRequestRequiredFields:
    @pytest.mark.parametrize("name", ["", "   "])
    def test_rejects_blank_name(self, name):
        with pytest.raises(ValidationError):
            SavedSegmentRequest(name=name, category_id="cat-1")

    @pytest.mark.parametrize("category_id", ["", "   "])
    def test_rejects_blank_category_id(self, category_id):
        with pytest.raises(ValidationError):
            SavedSegmentRequest(name="Subject", category_id=category_id)

    def test_accepts_required_fields(self):
        req = SavedSegmentRequest(name="Subject", category_id="cat-1")
        assert req.name == "Subject"
        assert req.category_id == "cat-1"


class TestRichSegmentPrefixSuffix:
    def test_defaults_to_none_when_omitted(self):
        segment = RichSegment(content="a fox")
        assert segment.prefix is None
        assert segment.suffix is None

    def test_accepts_byte_for_byte_prefix_and_suffix(self):
        segment = RichSegment(content="a fox", prefix="  (", suffix=", detailed)  ")
        assert segment.prefix == "  ("
        assert segment.suffix == ", detailed)  "

    def test_empty_string_becomes_none_like_color_and_description(self):
        segment = RichSegment(content="a fox", prefix="", suffix="")
        assert segment.prefix is None
        assert segment.suffix is None


class TestSavedSegmentRequestPrefixSuffix:
    def test_defaults_to_none_when_omitted(self):
        req = SavedSegmentRequest(name="Subject", category_id="cat-1")
        assert req.prefix is None
        assert req.suffix is None

    def test_accepts_prefix_and_suffix(self):
        req = SavedSegmentRequest(
            name="Subject", category_id="cat-1", prefix="(", suffix=")"
        )
        assert req.prefix == "("
        assert req.suffix == ")"


class TestSegmentTemplateRequestValidation:
    @pytest.mark.parametrize("name", ["", "   "])
    def test_rejects_blank_name(self, name):
        with pytest.raises(ValidationError):
            SegmentTemplateRequest(
                name=name, segments=[RichSegment(content="a fox", name="Subject")]
            )

    def test_rejects_empty_segments(self):
        with pytest.raises(ValidationError):
            SegmentTemplateRequest(name="My template", segments=[])

    def test_accepts_valid_request(self):
        req = SegmentTemplateRequest(
            name="My template", segments=[RichSegment(content="a fox", name="Subject")]
        )
        assert req.name == "My template"
        assert len(req.segments) == 1
