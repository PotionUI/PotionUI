"""
Tests for PathResolver.

This module tests the path resolution functionality for various resource types.
"""

import pytest
from src.platform.templating.path_resolver import PathResolver


class TestPathResolver:
    """Test cases for PathResolver."""

    @pytest.fixture
    def resolver(self):
        """PathResolver instance for testing."""
        return PathResolver()

    def test_default_paths(self, resolver):
        """Test that default path mappings are available."""
        expected_types = [
            "checkpoint",
            "lora",
            "embedding",
            "upscaler",
            "detector",
            "wildcard",
            "diffusion_model",
            "controlnet",
            "std",
        ]
        for path_type in expected_types:
            assert path_type in resolver.get_supported_types()

    def test_get_path_for_checkpoint(self, resolver):
        """Test get_path_for with checkpoint type."""
        result = resolver.get_path_for("checkpoint", "model.safetensors")
        assert result == "models/checkpoints/model.safetensors"

    def test_get_path_for_lora(self, resolver):
        """Test get_path_for with lora type."""
        result = resolver.get_path_for("lora", "style.safetensors")
        assert result == "models/loras/style.safetensors"

    def test_get_path_for_embedding(self, resolver):
        """Test get_path_for with embedding type."""
        result = resolver.get_path_for("embedding", "negative.pt")
        assert result == "models/embeddings/negative.pt"

    def test_get_path_for_without_filename(self, resolver):
        """Test get_path_for without filename returns base path."""
        result = resolver.get_path_for("upscaler")
        assert result == "models/upscalers"

    def test_get_path_for_invalid_type(self, resolver):
        """Test get_path_for with invalid type raises error."""
        with pytest.raises(ValueError, match="Unsupported path type: invalid_type"):
            resolver.get_path_for("invalid_type")

    def test_add_path_type(self, resolver):
        """Test adding a custom path type."""
        resolver.add_path_type("custom", "custom/path")
        result = resolver.get_path_for("custom", "file.txt")
        assert result == "custom/path/file.txt"

    def test_override_existing_path_type(self, resolver):
        """Test overriding an existing path type."""
        resolver.add_path_type("lora", "new/loras/path")
        result = resolver.get_path_for("lora", "style.safetensors")
        assert result == "new/loras/path/style.safetensors"

    def test_custom_paths_in_constructor(self):
        """Test passing custom paths in constructor."""
        custom_paths = {"custom": "my/custom/path"}
        resolver = PathResolver(custom_paths=custom_paths)

        result = resolver.get_path_for("custom", "file.txt")
        assert result == "my/custom/path/file.txt"

        # Default paths should still work
        result = resolver.get_path_for("lora", "style.safetensors")
        assert result == "models/loras/style.safetensors"

    def test_get_supported_types(self, resolver):
        """Test get_supported_types returns list of supported types."""
        types = resolver.get_supported_types()
        assert isinstance(types, list)
        assert len(types) == 9  # Default types
        assert "checkpoint" in types
        assert "lora" in types

    def test_all_default_paths(self, resolver):
        """Test all default path mappings."""
        expected = {
            "checkpoint": "models/checkpoints",
            "lora": "models/loras",
            "embedding": "models/embeddings",
            "upscaler": "models/upscalers",
            "detector": "models/detectors",
            "wildcard": "models/wildcards",
            "diffusion_model": "models/diffusion_models",
            "controlnet": "models/controlnet",
            "std": "src/std",
        }
        for path_type, expected_base in expected.items():
            result = resolver.get_path_for(path_type)
            assert result == expected_base, f"Failed for {path_type}"
