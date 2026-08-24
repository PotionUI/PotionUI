"""
Tests for the dict_utils helpers that survive the templating rework:
regex_search (backs the `matches`/`regex_search` filter) and
get_speed_profile_value (backs the `get_speed_profile` global).

The old dict-path helpers (get_value/get_is_in/get_dict_value/get_form_value)
backed the deleted `value`/`get`/`contains`/`dict`/`get_form` render globals
and are gone with them.
"""

import pytest
from src.platform.templating.dict_utils import (
    active_loras,
    get_speed_profile_value,
    regex_search,
)


class TestActiveLoras:
    """Test cases for active_loras, backing the `active_loras` filter."""

    def test_positive_strength_kept(self):
        loras = [{"model": "a.safetensors", "strength": 0.8}]
        assert active_loras(loras) == loras

    def test_zero_strength_dropped(self):
        assert active_loras([{"model": "a.safetensors", "strength": 0}]) == []
        assert active_loras([{"model": "a.safetensors", "strength": 0.0}]) == []

    def test_negative_strength_kept(self):
        """A negative strength is an inverted LoRA, not an inactive one."""
        loras = [{"model": "a.safetensors", "strength": -0.7}]
        assert active_loras(loras) == loras

    def test_missing_strength_kept(self):
        """No strength key means lora_picker's strength_default (1.0) applies."""
        loras = [{"model": "a.safetensors"}]
        assert active_loras(loras) == loras

    def test_string_zero_dropped(self):
        assert active_loras([{"model": "a.safetensors", "strength": "0"}]) == []
        assert active_loras([{"model": "a.safetensors", "strength": "0.0"}]) == []

    def test_string_nonzero_kept(self):
        loras = [{"model": "a.safetensors", "strength": "0.5"}]
        assert active_loras(loras) == loras

    def test_non_numeric_strength_kept(self):
        """Malformed input stays visible rather than vanishing silently."""
        for bad in ("strong", None, {}, []):
            loras = [{"model": "a.safetensors", "strength": bad}]
            assert active_loras(loras) == loras

    def test_non_dict_entry_kept(self):
        assert active_loras(["a.safetensors"]) == ["a.safetensors"]

    def test_mixed_list_preserves_order(self):
        keep_a = {"model": "a.safetensors", "strength": 1.0}
        keep_b = {"model": "b.safetensors", "strength": -0.5}
        keep_c = {"model": "c.safetensors"}
        loras = [
            keep_a,
            {"model": "zero.safetensors", "strength": 0},
            keep_b,
            {"model": "strzero.safetensors", "strength": "0"},
            keep_c,
        ]
        assert active_loras(loras) == [keep_a, keep_b, keep_c]

    def test_empty_list(self):
        assert active_loras([]) == []

    @pytest.mark.parametrize("value", [None, "loras", 5, {"model": "a"}])
    def test_non_list_returns_empty_list(self, value):
        assert active_loras(value) == []


class TestRegexSearch:
    """Test cases for regex_search function."""

    def test_match(self):
        """Test regex_search with matching pattern."""
        assert regex_search("hello world", r"wor\w+") is True

    def test_no_match(self):
        """Test regex_search with non-matching pattern."""
        assert regex_search("hello world", r"\d+") is False

    def test_complex_pattern(self):
        """Test regex_search with complex pattern."""
        email = "user@example.com"
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        assert regex_search(email, pattern) is True

    def test_partial_match(self):
        """Test regex_search with partial match."""
        assert regex_search("test123test", r"\d+") is True


class TestGetSpeedProfileValue:
    """Test cases for get_speed_profile_value function (roadmap 3.6)."""

    @pytest.fixture
    def sample_context(self):
        return {
            "preset": {
                "name": "My Preset",
                "speed_profiles": {
                    "draft": {"steps": 6, "guidance": 1.0},
                    "standard": {"steps": 28, "guidance": 5.0},
                },
            },
            "request": {"mode": "txt2img"},
        }

    def test_known_profile_returns_its_dict(self, sample_context):
        result = get_speed_profile_value(sample_context, "draft")
        assert result == {"steps": 6, "guidance": 1.0}

    def test_missing_profile_without_default_raises_with_preset_and_profile_name(self, sample_context):
        with pytest.raises(ValueError) as exc_info:
            get_speed_profile_value(sample_context, "turbo")
        message = str(exc_info.value)
        assert "My Preset" in message
        assert "turbo" in message
        assert "draft" in message and "standard" in message  # lists what IS declared

    def test_missing_profile_with_explicit_default_suppresses_error(self, sample_context):
        result = get_speed_profile_value(sample_context, "turbo", default={})
        assert result == {}

    def test_missing_profile_with_explicit_none_default_suppresses_error(self, sample_context):
        # None must be distinguishable from "no default given" - the sentinel exists for this.
        result = get_speed_profile_value(sample_context, "turbo", default=None)
        assert result is None

    def test_no_speed_profiles_declared_raises_with_unknown_preset_fallback(self):
        with pytest.raises(ValueError) as exc_info:
            get_speed_profile_value({}, "draft")
        assert "<unknown preset>" in str(exc_info.value)
        assert "draft" in str(exc_info.value)

    def test_empty_profiles_dict_with_default_returns_default(self):
        result = get_speed_profile_value({"preset": {"speed_profiles": {}}}, "draft", default="fallback")
        assert result == "fallback"
