"""PresetTemplateLoader.reload() - live rescan on plugin enable/disable.

Enabling/disabling a plugin used to require a backend restart before its
presets (or plugin-contributed modes) appeared/disappeared - nothing
ever told the loader to look again. `reload()` fixes that: same loader
instance, toggle the underlying registry's enabled set, call `reload()`,
observe the catalogue change - exactly the sequence `operations.
enable_plugin`/`disable_plugin` now perform (see test_operations.py's
preset/pipe rescan section for that wiring).

Also covers the atomicity guarantee `reload()` adds over `clear_cache()` +
`load_presets()`: `self.presets`/`self.load_errors` are swapped in as whole
new objects, never mutated in place mid-scan, so a concurrent unlocked reader
can't observe a half-rebuilt catalogue.
"""

from pathlib import Path

import yaml

from src.features.presets.loader import PresetTemplateLoader

MINIMAL_PIPELINE = "pipeline: []\n"
MINIMAL_FORM = "fields: []\n"


class _MutableRegistry:
    """A plugin registry whose enabled set can change between calls - unlike
    the frozen `_registry(enabled)` helper the sibling test files use, which
    bakes in one fixed set per loader instance and can't model a live toggle."""

    def __init__(self, enabled=()):
        self.enabled = list(enabled)

    def get_enabled_plugins(self):
        return list(self.enabled)


def _write_preset(root: Path, preset_id: str, modes: list[str]) -> Path:
    preset_dir = root / preset_id
    preset_dir.mkdir(parents=True)
    manifest = {
        "schema": 1, "id": preset_id, "name": preset_id, "version": "1.0.0",
        "category": "image", "engine": "native", "modes": modes,
    }
    (preset_dir / "preset.yml").write_text(yaml.dump(manifest))
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(MINIMAL_PIPELINE)
        (mode_dir / "form.yml").write_text(MINIMAL_FORM)
    return preset_dir


def _write_modes_root(plugin_dir: Path, modes: dict) -> Path:
    modes_root = plugin_dir / "contributed"
    for mode_name, pipeline_content in modes.items():
        mode_dir = modes_root / "modes" / mode_name
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(pipeline_content)
        (mode_dir / "form.yml").write_text(MINIMAL_FORM)
    return modes_root


def _plugin_owned_preset_manifest(plugin_id: str, plugin_dir: Path) -> object:
    from types import SimpleNamespace
    return SimpleNamespace(id=plugin_id, plugin_dir=plugin_dir, presets=[{"path": "presets"}], preset_modes=[])


# --- enable/disable makes a plugin-owned preset appear/disappear ------------


def test_reload_makes_newly_enabled_plugin_preset_appear(tmp_path):
    core_root = tmp_path / "core"
    core_root.mkdir()
    plugin_dir = tmp_path / "plugin"
    _write_preset(plugin_dir / "presets", "plugin-preset", ["txt2img"])
    manifest = _plugin_owned_preset_manifest("some-plugin", plugin_dir)

    registry = _MutableRegistry(enabled=[])
    loader = PresetTemplateLoader([str(core_root)], plugin_registry=registry)
    loader.load_presets()
    assert loader.load_preset_by_id("plugin-preset") is None

    registry.enabled = [manifest]
    loader.reload()

    assert loader.load_preset_by_id("plugin-preset") is not None


def test_reload_makes_disabled_plugin_preset_disappear(tmp_path):
    core_root = tmp_path / "core"
    core_root.mkdir()
    plugin_dir = tmp_path / "plugin"
    _write_preset(plugin_dir / "presets", "plugin-preset", ["txt2img"])
    manifest = _plugin_owned_preset_manifest("some-plugin", plugin_dir)

    registry = _MutableRegistry(enabled=[manifest])
    loader = PresetTemplateLoader([str(core_root)], plugin_registry=registry)
    loader.load_presets()
    assert loader.load_preset_by_id("plugin-preset") is not None

    registry.enabled = []
    loader.reload()

    assert loader.load_preset_by_id("plugin-preset") is None


# --- enable/disable makes a CONTRIBUTED MODE appear/disappear -----


def test_reload_makes_contributed_mode_appear_on_enable(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE})
    from types import SimpleNamespace
    manifest = SimpleNamespace(
        id="mode-plugin", plugin_dir=plugin_dir, presets=[],
        preset_modes=[{"target": "target-preset", "modes_root": "contributed"}],
    )

    registry = _MutableRegistry(enabled=[])
    loader = PresetTemplateLoader([str(core_root)], plugin_registry=registry)
    loader.load_presets()
    preset = loader.load_preset_by_id("target-preset")
    assert "img2img" not in preset.modes

    registry.enabled = [manifest]
    loader.reload()

    preset = loader.load_preset_by_id("target-preset")
    assert "img2img" in preset.modes
    assert preset.modes["img2img"].source_plugin == "mode-plugin"


def test_reload_makes_contributed_mode_disappear_on_disable(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE})
    from types import SimpleNamespace
    manifest = SimpleNamespace(
        id="mode-plugin", plugin_dir=plugin_dir, presets=[],
        preset_modes=[{"target": "target-preset", "modes_root": "contributed"}],
    )

    registry = _MutableRegistry(enabled=[manifest])
    loader = PresetTemplateLoader([str(core_root)], plugin_registry=registry)
    loader.load_presets()
    assert "img2img" in loader.load_preset_by_id("target-preset").modes

    registry.enabled = []
    loader.reload()

    assert "img2img" not in loader.load_preset_by_id("target-preset").modes


# --- atomicity: reload() swaps in whole new containers, never mutates live ones


def test_reload_swaps_in_new_container_objects_not_in_place_mutation():
    """The mechanism that makes a concurrent unlocked read safe: the OLD
    `presets` list a reader might be mid-iteration over is never touched by a
    `reload()` running on another thread - only replaced by reassignment."""
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    old_presets_obj = loader.presets
    old_errors_obj = loader.load_errors
    old_snapshot = list(old_presets_obj)  # what a concurrent reader would see

    loader.reload()

    assert loader.presets is not old_presets_obj
    assert loader.load_errors is not old_errors_obj
    # The old object a concurrent reader might still be iterating is untouched.
    assert old_presets_obj == old_snapshot


def test_reload_sets_loaded_true_even_from_a_never_loaded_state():
    loader = PresetTemplateLoader(["content/presets"])
    assert loader._loaded is False
    loader.reload()
    assert loader._loaded is True
    assert len(loader.presets) > 0


def test_clear_cache_empties_immediately_reload_does_not(tmp_path):
    """Documents the deliberate behavioral difference: clear_cache() is the
    legacy lazy-invalidate (empties now, next access rebuilds); reload() never
    leaves the catalogue observably empty."""
    core_root = tmp_path / "presets"
    _write_preset(core_root, "preset-one", ["txt2img"])
    loader = PresetTemplateLoader([str(core_root)])
    loader.load_presets()
    assert len(loader.presets) == 1

    loader.clear_cache()
    assert loader.presets == []  # legacy contract: empty right away

    loader.load_presets()
    assert len(loader.presets) == 1

    loader.reload()
    assert len(loader.presets) == 1  # never empty at any point reload() itself runs
