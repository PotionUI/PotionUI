"""`PresetTemplateLoader.on_presets_changed` - the hook bootstrap wires a
`PresetMediaPrerenderQueue` onto (see `src/bootstrap/container.py`) so a
media pre-render scan follows every load/reload, not just the first one.
"""

from pathlib import Path

from src.features.presets.loader import PresetTemplateLoader


def _write_preset(root: Path, model: str, preset_id: str) -> Path:
    preset_dir = root / model / "std"
    preset_dir.mkdir(parents=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test Preset"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
"""
    )
    (preset_dir / "modes" / "txt2img").mkdir(parents=True)
    (preset_dir / "modes" / "txt2img" / "pipeline.yml").write_text("pipeline: []\n")
    return preset_dir


class TestOnPresetsChangedCallback:
    def test_fires_on_initial_lazy_load(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        _write_preset(root, "Model", "01AAAAAAAAAAAAAAAAAAAAAAAAA")

        loader = PresetTemplateLoader([str(root)])
        calls = []
        loader.on_presets_changed = calls.append

        # Nothing has forced a load yet - accessing get_all_presets() is what
        # triggers the lazy `_ensure_loaded()` path (the initial startup scan).
        loader.get_all_presets()

        assert len(calls) == 1
        assert {p.id for p in calls[0]} == {"01AAAAAAAAAAAAAAAAAAAAAAAAA"}

    def test_fires_again_on_explicit_reload(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        _write_preset(root, "Model", "01BBBBBBBBBBBBBBBBBBBBBBBBB")

        loader = PresetTemplateLoader([str(root)])
        loader.load_presets()

        calls = []
        loader.on_presets_changed = calls.append
        loader.reload()

        assert len(calls) == 1

    def test_fires_on_load_presets_after_new_preset_added(self, tmp_path):
        """The admin "reload preset" path (`clear_cache()` + `load_presets()`)
        must also trigger a fresh pre-render scan, picking up a preset added
        since the last load."""
        root = tmp_path / "presets"
        root.mkdir()
        _write_preset(root, "Model", "01CCCCCCCCCCCCCCCCCCCCCCCCC")

        loader = PresetTemplateLoader([str(root)])
        loader.load_presets()

        _write_preset(root, "OtherModel", "01DDDDDDDDDDDDDDDDDDDDDDDDD")

        calls = []
        loader.on_presets_changed = calls.append
        loader.clear_cache()
        loader.load_presets()

        assert len(calls) == 1
        assert {p.id for p in calls[0]} == {
            "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            "01DDDDDDDDDDDDDDDDDDDDDDDDD",
        }

    def test_a_failing_callback_does_not_break_the_load(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        _write_preset(root, "Model", "01EEEEEEEEEEEEEEEEEEEEEEEEE")

        loader = PresetTemplateLoader([str(root)])

        def boom(_presets):
            raise RuntimeError("prerender queue exploded")

        loader.on_presets_changed = boom

        # Must not raise - a callback failure is best-effort, never fatal to
        # the preset load itself.
        loader.load_presets()

        assert {p.id for p in loader.presets} == {"01EEEEEEEEEEEEEEEEEEEEEEEEE"}

    def test_default_callback_is_none_and_load_works_without_one(self, tmp_path):
        root = tmp_path / "presets"
        root.mkdir()
        _write_preset(root, "Model", "01FFFFFFFFFFFFFFFFFFFFFFFFF")

        loader = PresetTemplateLoader([str(root)])
        assert loader.on_presets_changed is None
        loader.load_presets()  # must not raise with no callback wired
