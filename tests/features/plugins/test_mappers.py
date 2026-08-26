"""
Unit tests for the plugins feature's response mappers.

Only `plugin_to_response` has real logic (registry-manifest enrichment with
fallback defaults) - `hook_to_response`/`setting_to_response` are dumb field
copies covered indirectly by the route/manager tests that call them.
"""
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from src.features.plugins.mappers import plugin_to_response
from src.features.plugins.records import Plugin
from src.platform.plugins.loader import PluginManifest


def _sample_plugin() -> Plugin:
    return Plugin(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        type="full-stack",
        enabled=True,
        manifest_path="/content/plugins/local/test-plugin/manifest.yml",
        description="A test plugin",
        author="Test Author",
        installed_at=datetime(2024, 1, 1, 12, 0, 0),
        updated_at=datetime(2024, 1, 1, 12, 0, 0),
    )


def test_plugin_to_response_enriches_from_manifest():
    """plugin_to_response pulls category/tags/capabilities/counts from the manifest"""
    registry = Mock()
    manifest = PluginManifest(
        id="test-plugin-1",
        name="Test Plugin",
        version="1.0.0",
        description="A test plugin",
        author="Test Author",
        plugin_type="full-stack",
        category="media",
        tags=["editing", "video"],
        capabilities=["image-enhancement"],
        source="marketplace",
        homepage="https://example.com",
        repository="https://github.com/example/test-plugin",
        hooks={"pre_generation": "hooks/pre_generation.py"},
        frontend_hooks=[{"hook_name": "workbench.image_modal"}],
        settings=[{"name": "api_key", "type": "string"}, {"name": "timeout", "type": "int"}],
        manifest_path=Path("/content/plugins/marketplace/test-plugin/manifest.yml"),
        plugin_dir=Path("/content/plugins/marketplace/test-plugin"),
    )
    registry.get_plugin.return_value = manifest
    registry.get_plugin_state.return_value = None
    registry.get_plugin_error.return_value = None

    result = plugin_to_response(_sample_plugin(), registry)

    assert result.category == "media"
    assert result.tags == ["editing", "video"]
    assert result.capabilities == ["image-enhancement"]
    assert result.source == "marketplace"
    assert result.homepage == "https://example.com"
    assert result.repository == "https://github.com/example/test-plugin"
    assert result.hook_count == 2  # 1 backend hook + 1 frontend hook
    assert result.settings_count == 2


def test_plugin_to_response_falls_back_to_defaults_without_manifest():
    """plugin_to_response falls back to safe defaults when the registry has no manifest"""
    registry = Mock()
    registry.get_plugin.return_value = None
    registry.get_plugin_state.return_value = None
    registry.get_plugin_error.return_value = None

    result = plugin_to_response(_sample_plugin(), registry)

    assert result.category == "other"
    assert result.tags == []
    assert result.capabilities == []
    assert result.source == "local"
    assert result.homepage is None
    assert result.repository is None
    assert result.hook_count == 0
    assert result.settings_count == 0
