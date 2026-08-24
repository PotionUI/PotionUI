"""Presets contributed by a plugin's `presets:` manifest root.

The real comfyui-backend fixture cases moved with that plugin to its own
tests/ (content/plugins/marketplace/comfyui-backend is out of tree); these
are the mechanism tests that only need a synthetic manifest.
"""

from pathlib import Path
from types import SimpleNamespace

from src.platform.plugins.manifest import PluginManifestSchema
from src.features.presets.loader import PresetTemplateLoader, plugin_preset_roots

COMFYUI_PRESET_ID = "01K4TDMABXKD1RR4CGBM51QWEN"


def _registry(enabled):
    return SimpleNamespace(get_enabled_plugins=lambda: list(enabled))


def test_manifest_schema_accepts_presets_root():
    schema = PluginManifestSchema.model_validate({
        "id": "p", "name": "P", "version": "1.0.0", "description": "d",
        "author": "a", "type": "backend-only",
        "presets": [{"path": "presets"}],
    })
    assert [r.path for r in schema.presets] == ["presets"]


def test_plugin_preset_roots_resolves_against_plugin_dir():
    manifest = SimpleNamespace(
        presets=[{"path": "presets"}],
        plugin_dir=Path("content/plugins/marketplace/some-plugin"),
    )
    roots = plugin_preset_roots([manifest])
    assert roots == [Path("content/plugins/marketplace/some-plugin/presets").resolve()]


def test_manifest_without_presets_contributes_no_roots():
    manifest = SimpleNamespace(presets=[], plugin_dir=Path("content/plugins/marketplace/some-plugin"))
    assert plugin_preset_roots([manifest]) == []


def test_disabled_plugin_presets_are_not_loaded():
    loader = PresetTemplateLoader(["content/presets"], plugin_registry=_registry([]))
    loader.load_presets()
    loaded = {p.id for p in loader.presets}
    assert COMFYUI_PRESET_ID not in loaded
