"""The nvidia-rtx-upscale manifest must parse under the real `PluginLoader`,
not just under `yaml.safe_load` - `PluginManifestSchema` validation
(extra="forbid" on most sections) is what would actually catch a typo
introduced while renaming the plugin id, and only `PluginLoader._load_manifest`
exercises it end to end.

This lives here rather than in the plugin's own `content/plugins/marketplace/
nvidia-rtx-upscale/tests/` - a marketplace plugin's own tests dir may only
import `src.plugin_api` (tests/architecture/test_layering.py's `content/plugins`
scope), and `PluginLoader` is a core-internal that plugin_api deliberately
does not expose. comfyui-backend and runpod-provider follow the same split:
their own tests/ trees stay plugin_api-only; anything needing a core
internal directly lives in this top-level mirror instead.
"""

from pathlib import Path

import pytest

from src.platform.plugins.loader import PluginLoader

PLUGIN_ROOT = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "nvidia-rtx-upscale"


@pytest.fixture(scope="session")
def loaded_manifest():
    loader = PluginLoader(marketplace_dir=str(PLUGIN_ROOT), local_dir=str(PLUGIN_ROOT))
    manifest = loader._load_manifest(
        PLUGIN_ROOT / "manifest.yml", PLUGIN_ROOT, source="local",
    )
    assert manifest is not None, "manifest.yml produced no PluginManifest at all"
    return manifest


def test_manifest_parses_with_no_validation_error(loaded_manifest):
    assert loaded_manifest.validation_error is None, loaded_manifest.validation_error


def test_manifest_id_carries_the_vendor_prefix(loaded_manifest):
    assert loaded_manifest.id == "nvidia-rtx-upscale"
    assert loaded_manifest.plugin_dir.name == "nvidia-rtx-upscale"


def test_manifest_declares_no_dependencies_block(loaded_manifest):
    """Deliberate - see the rationale comment in manifest.yml: `nvidia-vfx`
    (PyPI name) vs `nvvfx` (import name) makes `dependencies.python` unusable
    here without falsely gating enable."""
    assert loaded_manifest.dependencies_python == []
    assert loaded_manifest.dependencies_binaries == []


def test_manifest_pipe_entry_resolves_to_a_real_module_on_disk(loaded_manifest):
    assert loaded_manifest.pipes == [
        {"path": "pipes/upscaler_rtx", "register_as": "upscaler/rtx_vsr"}
    ]

    pipe_dir = PLUGIN_ROOT / loaded_manifest.pipes[0]["path"]
    assert (pipe_dir / "main.py").is_file()


def test_manifest_preset_root_resolves_to_the_real_preset_on_disk(loaded_manifest):
    assert loaded_manifest.presets == [{"path": "presets"}]

    preset_root = PLUGIN_ROOT / loaded_manifest.presets[0]["path"]
    preset_file = preset_root / "RTX-Upscale" / "preset.yml"
    assert preset_file.is_file()

    import yaml

    preset = yaml.safe_load(preset_file.read_text())
    assert preset["engine"] == "native"
    requirement_types = {entry["type"] for entry in preset.get("requirements", [])}
    assert requirement_types == {"python_package", "platform"}
