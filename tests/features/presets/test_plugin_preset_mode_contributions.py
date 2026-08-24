"""Modes a plugin contributes to an EXISTING preset via `preset_modes:`
- distinct from `presets:` (a plugin shipping a whole new preset;
see test_plugin_preset_discovery.py). All cases here are self-contained
(tmp_path); the real-fixture case (krea2-edit contributing onto the native
Krea2 preset) moved with that plugin to its own tests/ when it left the core
tree."""

from pathlib import Path
from types import SimpleNamespace

import yaml

from src.platform.plugins.manifest import PluginManifestSchema
from src.features.presets.loader import (
    PresetTemplateLoader,
    plugin_preset_mode_contributions,
)

MINIMAL_PIPELINE = "pipeline: []\n"
MINIMAL_FORM = "fields: []\n"
BROKEN_PIPELINE = "pipeline:\n  - not_a_recognized_key: true\n"


def _registry(enabled):
    return SimpleNamespace(get_enabled_plugins=lambda: list(enabled))


def _write_preset(root: Path, preset_id: str, modes: list[str]) -> Path:
    """A minimal, schema-valid core preset with one empty mode per name in `modes`."""
    preset_dir = root / preset_id
    preset_dir.mkdir(parents=True)
    manifest = {
        "schema": 1,
        "id": preset_id,
        "name": preset_id,
        "version": "1.0.0",
        "category": "image",
        "engine": "native",
        "modes": modes,
    }
    (preset_dir / "preset.yml").write_text(yaml.dump(manifest))
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(MINIMAL_PIPELINE)
        (mode_dir / "form.yml").write_text(MINIMAL_FORM)
    return preset_dir


def _write_modes_root(plugin_dir: Path, modes: dict[str, str]) -> Path:
    """A plugin's `modes_root` dir: `modes/<name>/{pipeline.yml,form.yml}` per
    entry in `modes` (name -> pipeline.yml content, so a test can inject a
    broken one)."""
    modes_root = plugin_dir / "contributed"
    for mode_name, pipeline_content in modes.items():
        mode_dir = modes_root / "modes" / mode_name
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text(pipeline_content)
        (mode_dir / "form.yml").write_text(MINIMAL_FORM)
    return modes_root


def _manifest(plugin_id: str, plugin_dir: Path, entries: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(id=plugin_id, plugin_dir=plugin_dir, preset_modes=entries)


# --- manifest schema ---------------------------------------------------------


def test_manifest_schema_accepts_preset_modes():
    schema = PluginManifestSchema.model_validate({
        "id": "p", "name": "P", "version": "1.0.0", "description": "d",
        "author": "a", "type": "backend-only",
        "preset_modes": [{"target": "some-preset", "modes_root": "contributed"}],
    })
    assert [(e.target, e.modes_root) for e in schema.preset_modes] == [("some-preset", "contributed")]


def test_manifest_schema_rejects_unknown_keys_in_preset_modes_entry():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PluginManifestSchema.model_validate({
            "id": "p", "name": "P", "version": "1.0.0", "description": "d",
            "author": "a", "type": "backend-only",
            "preset_modes": [{"target": "x", "modes_root": "y", "bogus": True}],
        })


# --- plugin_preset_mode_contributions ---------------------------------------


def test_plugin_preset_mode_contributions_resolves_against_plugin_dir(tmp_path):
    manifest = _manifest("plugin-a", tmp_path / "plugin-a", [{"target": "tgt", "modes_root": "contributed"}])
    contributions = plugin_preset_mode_contributions([manifest])
    assert len(contributions) == 1
    assert contributions[0].plugin_id == "plugin-a"
    assert contributions[0].target_preset_id == "tgt"
    assert contributions[0].modes_root == (tmp_path / "plugin-a" / "contributed").resolve()


def test_manifest_without_preset_modes_contributes_nothing(tmp_path):
    manifest = _manifest("plugin-a", tmp_path / "plugin-a", [])
    assert plugin_preset_mode_contributions([manifest]) == []


def test_contributions_sorted_by_plugin_id_regardless_of_input_order(tmp_path):
    manifest_b = _manifest("plugin-b", tmp_path / "plugin-b", [{"target": "tgt", "modes_root": "m"}])
    manifest_a = _manifest("plugin-a", tmp_path / "plugin-a", [{"target": "tgt", "modes_root": "m"}])
    contributions = plugin_preset_mode_contributions([manifest_b, manifest_a])
    assert [c.plugin_id for c in contributions] == ["plugin-a", "plugin-b"]


def test_declaration_order_preserved_within_one_plugin(tmp_path):
    manifest = _manifest("plugin-a", tmp_path / "plugin-a", [
        {"target": "tgt-2", "modes_root": "m2"},
        {"target": "tgt-1", "modes_root": "m1"},
    ])
    contributions = plugin_preset_mode_contributions([manifest])
    assert [c.target_preset_id for c in contributions] == ["tgt-2", "tgt-1"]


# --- loader merge: happy path -------------------------------------------------


def test_contributed_mode_merges_into_target_preset(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    modes_root = _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE})
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img", "img2img"}
    assert target.modes["txt2img"].source_plugin is None
    assert target.modes["img2img"].source_plugin == "some-plugin"
    assert loader.load_errors == {}


def test_contributed_mode_absent_when_target_preset_missing(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE})
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "no-such-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    assert loader.load_errors == {}
    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img"}


def test_contributed_mode_absent_when_plugin_disabled(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE})

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img"}


def test_no_plugin_registry_skips_contributions_without_error(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])

    loader = PresetTemplateLoader([str(core_root)])  # plugin_registry=None
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img"}


# --- collisions ---------------------------------------------------------------


def test_contribution_colliding_with_core_mode_is_rejected_core_stays_intact(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    modes_root = _write_modes_root(plugin_dir, {})
    # Contribute a mode literally named "txt2img" - collides with the core mode.
    (modes_root / "modes" / "txt2img").mkdir(parents=True)
    (modes_root / "modes" / "txt2img" / "pipeline.yml").write_text(MINIMAL_PIPELINE)
    (modes_root / "modes" / "txt2img" / "form.yml").write_text(MINIMAL_FORM)
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert target.modes["txt2img"].source_plugin is None  # core mode untouched
    assert any("some-plugin" in key for key in loader.load_errors)
    error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
    assert "collides with a core mode" in error_text


def test_two_plugins_colliding_first_by_plugin_id_wins(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])

    plugin_a_dir = tmp_path / "plugin-a"
    _write_modes_root(plugin_a_dir, {"extra": MINIMAL_PIPELINE})
    plugin_b_dir = tmp_path / "plugin-b"
    _write_modes_root(plugin_b_dir, {"extra": MINIMAL_PIPELINE})

    manifest_a = _manifest("plugin-a", plugin_a_dir, [{"target": "target-preset", "modes_root": "contributed"}])
    manifest_b = _manifest("plugin-b", plugin_b_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    # Registry returns them out of alphabetical order - resolution must not
    # depend on that.
    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest_b, manifest_a]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert target.modes["extra"].source_plugin == "plugin-a"
    assert any("plugin-b" in key for key in loader.load_errors)
    error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
    assert "already contributed by plugin 'plugin-a'" in error_text


def test_self_collision_within_one_plugin_first_declared_wins(tmp_path):
    """Two `preset_modes:` entries from the SAME plugin whose modes_roots both
    happen to define a mode with the same name - the same first-wins rule
    applies, no special-casing "self" vs "other"."""
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])

    plugin_dir = tmp_path / "plugin-a"
    root_one = _write_modes_root(plugin_dir / "one", {"extra": MINIMAL_PIPELINE})
    root_two = _write_modes_root(plugin_dir / "two", {"extra": MINIMAL_PIPELINE})

    manifest = _manifest("plugin-a", plugin_dir, [
        {"target": "target-preset", "modes_root": "one/contributed"},
        {"target": "target-preset", "modes_root": "two/contributed"},
    ])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert target.modes["extra"].source_plugin == "plugin-a"
    assert any("already contributed by plugin 'plugin-a'" in msg for msgs in loader.load_errors.values() for msg in msgs)


# --- invalid contributions -----------------------------------------------------


def test_invalid_contributed_mode_is_rejected_target_preset_unaffected(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": BROKEN_PIPELINE})
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img"}
    assert any("some-plugin" in key and "img2img" in key for key in loader.load_errors)


def test_missing_modes_directory_under_modes_root_is_a_load_error(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "contributed").mkdir()  # exists, but no modes/ subdir inside
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img"}
    assert any("some-plugin" in key for key in loader.load_errors)
    error_text = " ".join(msg for msgs in loader.load_errors.values() for msg in msgs)
    assert "has no modes/ directory" in error_text


def test_multiple_contributed_modes_from_one_plugin_all_merge(tmp_path):
    core_root = tmp_path / "presets"
    _write_preset(core_root, "target-preset", ["txt2img"])
    plugin_dir = tmp_path / "plugin"
    _write_modes_root(plugin_dir, {"img2img": MINIMAL_PIPELINE, "upscale": MINIMAL_PIPELINE})
    manifest = _manifest("some-plugin", plugin_dir, [{"target": "target-preset", "modes_root": "contributed"}])

    loader = PresetTemplateLoader([str(core_root)], plugin_registry=_registry([manifest]))
    loader.load_presets()

    target = next(p for p in loader.presets if p.id == "target-preset")
    assert set(target.modes.keys()) == {"txt2img", "img2img", "upscale"}
    assert target.modes["img2img"].source_plugin == "some-plugin"
    assert target.modes["upscale"].source_plugin == "some-plugin"


NATIVE_KREA2_PRESET_ID = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"


def test_krea2_edit_mode_absent_when_plugin_disabled():
    loader = PresetTemplateLoader(["content/presets"], plugin_registry=_registry([]))
    loader.load_presets()

    krea2 = next(p for p in loader.presets if p.id == NATIVE_KREA2_PRESET_ID)
    assert "edit" not in krea2.modes
    assert set(krea2.modes.keys()) == {"txt2img", "enhance"}
