"""
Tests for IconMapper.

This module tests the icon mapping functionality for UI elements.
"""

import pytest
from src.platform.templating.icon_mapper import IconMapper


class TestIconMapper:
    """Test cases for IconMapper."""

    @pytest.fixture
    def mapper(self):
        """IconMapper instance for testing."""
        return IconMapper()

    def test_predefined_mappings(self, mapper):
        """Test predefined icon mappings."""
        expected = {
            "prompt": "pencil-square",
            "lora": "puzzle-piece",
            "controlnet": "viewfinder-circle",
            "advanced": "cog-6-tooth",
            "face_detection": "face-smile",
            "input": "photo",
            "enhancement": "sparkles",
            "upscale": "arrows-pointing-out",
            "lighting": "sun",
            "composition": "squares-2x2",
            "style": "paint-brush",
            "quality": "star",
            "model": "cube",
            "settings": "adjustments-horizontal",
            "output": "document-arrow-down",
            "generation": "bolt",
            "processing": "cpu-chip",
            "filters": "funnel",
            "effects": "sparkles",
        }
        for icon_type, expected_icon in expected.items():
            result = mapper.get_icon(icon_type)
            assert result == expected_icon, f"Failed for {icon_type}"

    def test_case_insensitive(self, mapper):
        """Test get_icon is case insensitive."""
        assert mapper.get_icon("PROMPT") == "pencil-square"
        assert mapper.get_icon("Lora") == "puzzle-piece"
        assert mapper.get_icon("ControlNet") == "viewfinder-circle"
        assert mapper.get_icon("ADVANCED") == "cog-6-tooth"

    def test_custom_icon_passthrough(self, mapper):
        """Test that unknown icon types are passed through."""
        result = mapper.get_icon("custom-icon-name")
        assert result == "custom-icon-name"

        result = mapper.get_icon("my-special-icon")
        assert result == "my-special-icon"

    def test_add_icon_mapping(self, mapper):
        """Test adding a custom icon mapping."""
        mapper.add_icon_mapping("custom", "star-outline")
        result = mapper.get_icon("custom")
        assert result == "star-outline"

    def test_override_existing_mapping(self, mapper):
        """Test overriding an existing icon mapping."""
        mapper.add_icon_mapping("prompt", "pencil")
        result = mapper.get_icon("prompt")
        assert result == "pencil"

    def test_custom_mappings_in_constructor(self):
        """Test passing custom mappings in constructor."""
        custom = {"custom": "my-icon"}
        mapper = IconMapper(custom_mappings=custom)

        result = mapper.get_icon("custom")
        assert result == "my-icon"

        # Default mappings should still work
        result = mapper.get_icon("prompt")
        assert result == "pencil-square"

    def test_get_all_mappings(self, mapper):
        """Test get_all_mappings returns a copy of mappings."""
        mappings = mapper.get_all_mappings()
        assert isinstance(mappings, dict)
        assert "prompt" in mappings
        assert mappings["prompt"] == "pencil-square"

        # Verify it's a copy (modifying it doesn't affect original)
        mappings["prompt"] = "modified"
        assert mapper.get_icon("prompt") == "pencil-square"

    def test_add_mapping_case_normalized(self, mapper):
        """Test that added mappings are normalized to lowercase."""
        mapper.add_icon_mapping("CUSTOM_TYPE", "custom-icon")
        # Should be accessible with any case
        assert mapper.get_icon("custom_type") == "custom-icon"
        assert mapper.get_icon("CUSTOM_TYPE") == "custom-icon"
