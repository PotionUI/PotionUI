"""Tests for src.features.developer.operations."""


class TestGetPresetsLint:
    """`get_presets_lint` (`GET /api/developer/presets/lint`) must feed the
    preset_loader's enabled plugins into `PresetLinter`, so a
    plugin `preset_modes:` collision surfaces here without a separate code
    path from `scripts/preset_lint.py`."""

    @staticmethod
    def _write_preset(root, preset_id, modes):
        import yaml

        preset_dir = root / preset_id
        preset_dir.mkdir(parents=True)
        (preset_dir / "preset.yml").write_text(yaml.dump({
            "schema": 1, "id": preset_id, "name": preset_id, "version": "1.0.0",
            "category": "image", "engine": "native", "modes": modes,
        }))
        for mode in modes:
            mode_dir = preset_dir / "modes" / mode
            mode_dir.mkdir(parents=True)
            (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        return preset_dir

    @staticmethod
    def _write_modes_root(plugin_dir, mode_name):
        mode_dir = plugin_dir / "contributed" / "modes" / mode_name
        mode_dir.mkdir(parents=True)
        (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
        return plugin_dir / "contributed"

    def _real_loader(self, tmp_path, plugin_registry):
        from src.features.presets.loader import PresetTemplateLoader

        core_root = tmp_path / "presets"
        self._write_preset(core_root, "target-preset", ["txt2img"])
        return PresetTemplateLoader([str(core_root)], plugin_registry=plugin_registry)

    def test_no_plugin_registry_lints_without_crashing(self, tmp_path):
        from src.features.developer import operations

        loader = self._real_loader(tmp_path, plugin_registry=None)
        result = operations.get_presets_lint(loader)
        assert result["load_errors"] == {}

    def test_plugin_registry_collision_surfaces_as_lint_issue(self, tmp_path):
        from types import SimpleNamespace
        from src.features.developer import operations

        plugin_dir = tmp_path / "plugin"
        self._write_modes_root(plugin_dir, "txt2img")  # collides with the target's core mode
        manifest = SimpleNamespace(
            id="some-plugin", plugin_dir=plugin_dir,
            preset_modes=[{"target": "target-preset", "modes_root": "contributed"}],
        )
        registry = SimpleNamespace(get_enabled_plugins=lambda: [manifest])

        loader = self._real_loader(tmp_path, plugin_registry=registry)
        result = operations.get_presets_lint(loader)

        assert any(
            issue["level"] == "error" and "collides with a core mode" in issue["message"]
            for issue in result["lint_issues"]
        )
