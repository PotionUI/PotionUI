from pathlib import Path

import pytest

from src.features.prompt.resource_sources import prompt_resource_sources
from src.platform.plugins.registry import PluginRegistry

FIXTURE_PLUGINS = Path(__file__).resolve().parents[1] / "e2e" / "plugins"
FIXTURE_PLUGIN_ID = "e2e-preset-extension"
FAKE_SOURCE = "fixture-bundle"


@pytest.fixture
def fake_prompt_source(tmp_path):
    registry = PluginRegistry(
        str(FIXTURE_PLUGINS), str(tmp_path / "local"), prompt_resource_source_registry=prompt_resource_sources(),
    )
    assert registry.enable_plugin(FIXTURE_PLUGIN_ID), registry.get_plugin_error(FIXTURE_PLUGIN_ID)
    source = prompt_resource_sources().get(FAKE_SOURCE)
    calls = registry.loader.load_plugin_module(registry.get_plugin(FIXTURE_PLUGIN_ID), "sources").CALLS
    calls.clear()
    try:
        yield source, calls
    finally:
        registry.disable_plugin(FIXTURE_PLUGIN_ID)
        assert prompt_resource_sources().get(FAKE_SOURCE) is None
