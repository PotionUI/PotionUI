"""Tests for GenerationRequest DTO with multi-prompt support."""

import pytest
from pydantic import ValidationError

from src.features.generation.dto import GenerationRequest, PromptPair, RatingRequest


class TestPromptPair:
    """Tests for PromptPair model."""

    def test_prompt_pair_defaults(self):
        """Test PromptPair default values."""
        pair = PromptPair()
        assert pair.positive == ""
        assert pair.negative == ""

    def test_prompt_pair_with_values(self):
        """Test PromptPair with values."""
        pair = PromptPair(positive="test positive", negative="test negative")
        assert pair.positive == "test positive"
        assert pair.negative == "test negative"


class TestGenerationRequestMultiPrompt:
    """Tests for GenerationRequest multi-prompt support."""

    def test_legacy_format_converted_to_array(self):
        """Test that legacy prompt/negative_prompt fields are converted to prompts array."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompt="positive prompt",
            negative_prompt="negative prompt"
        )

        assert request.prompts is not None
        assert len(request.prompts) == 1
        assert request.prompts[0].positive == "positive prompt"
        assert request.prompts[0].negative == "negative prompt"

    def test_legacy_format_empty_prompts(self):
        """Test that legacy format with empty prompts creates array."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompt="",
            negative_prompt=""
        )

        assert request.prompts is not None
        assert len(request.prompts) == 1
        assert request.prompts[0].positive == ""
        assert request.prompts[0].negative == ""

    def test_prompts_array_format(self):
        """Test that prompts array is preserved."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[
                PromptPair(positive="prompt 1", negative="neg 1"),
                PromptPair(positive="prompt 2", negative="neg 2")
            ]
        )

        assert len(request.prompts) == 2
        assert request.prompts[0].positive == "prompt 1"
        assert request.prompts[0].negative == "neg 1"
        assert request.prompts[1].positive == "prompt 2"
        assert request.prompts[1].negative == "neg 2"

    def test_prompts_array_from_dicts(self):
        """Test that prompts array from dicts is converted to PromptPair."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[
                {"positive": "dict prompt 1", "negative": "dict neg 1"},
                {"positive": "dict prompt 2", "negative": "dict neg 2"}
            ]
        )

        assert len(request.prompts) == 2
        assert isinstance(request.prompts[0], PromptPair)
        assert request.prompts[0].positive == "dict prompt 1"
        assert request.prompts[1].positive == "dict prompt 2"

    def test_prompts_takes_precedence_over_legacy(self):
        """Test that prompts array takes precedence over legacy fields."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompt="legacy prompt",
            negative_prompt="legacy negative",
            prompts=[
                PromptPair(positive="array prompt", negative="array negative")
            ]
        )

        # prompts array should be used, not legacy fields
        assert len(request.prompts) == 1
        assert request.prompts[0].positive == "array prompt"
        assert request.prompts[0].negative == "array negative"

    def test_legacy_fields_still_accessible(self):
        """Test that legacy prompt fields are still accessible."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompt="test prompt",
            negative_prompt="test negative"
        )

        # Legacy fields should still be readable
        assert request.prompt == "test prompt"
        assert request.negative_prompt == "test negative"

    def test_empty_prompts_array_uses_legacy(self):
        """Test that None prompts uses legacy format."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompt="fallback prompt",
            negative_prompt="fallback negative",
            prompts=None
        )

        assert len(request.prompts) == 1
        assert request.prompts[0].positive == "fallback prompt"
        assert request.prompts[0].negative == "fallback negative"

    def test_form_data_preserved(self):
        """Test that form_data is preserved with multi-prompt."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
            form_data={"steps": 20, "cfg": 7.5}
        )

        assert request.form_data == {"steps": 20, "cfg": 7.5}

    def test_mode_preserved(self):
        """Test that mode is preserved with multi-prompt."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
            mode="img2img"
        )

        assert request.mode == "img2img"

    def test_backend_id_preserved(self):
        """Test that backend_id is preserved with multi-prompt."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
            backend_id="custom-backend"
        )

        assert request.backend_id == "custom-backend"

    def test_form_name_defaults_to_none(self):
        """Test that form_name (preset "variant" selection) defaults to None
        when omitted, so the mode's default variant is resolved downstream."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
        )

        assert request.form_name is None

    def test_form_name_preserved(self):
        """Test that an explicit form_name is preserved."""
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
            form_name="advanced"
        )

        assert request.form_name == "advanced"


class TestGenerationRequestSourcePromptId:
    """`source_prompt_id` is a top-level field (Prompt Library provenance),
    never nested inside form_data."""

    def test_defaults_to_none(self):
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
        )

        assert request.source_prompt_id is None

    def test_top_level_value_preserved(self):
        request = GenerationRequest(
            preset_id="test-preset",
            prompts=[PromptPair(positive="test", negative="")],
            source_prompt_id="prompt-123",
            form_data={"prompt": "unrelated"},
        )

        assert request.source_prompt_id == "prompt-123"
        assert "source_prompt_id" not in request.form_data


class TestRatingRequest:
    """Tests for RatingRequest range validation."""

    @pytest.mark.parametrize("rating", [0, 1, 5])
    def test_accepts_in_range_rating(self, rating):
        req = RatingRequest(rating=rating)
        assert req.rating == rating

    @pytest.mark.parametrize("rating", [-1, 6])
    def test_rejects_out_of_range_rating(self, rating):
        with pytest.raises(ValidationError):
            RatingRequest(rating=rating)
