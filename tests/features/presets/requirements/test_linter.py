"""PresetLinter's `requirements:` checks: unknown type and per-type schema
validation - see `PresetLinter._lint_requirements`.
"""

import yaml

from src.features.presets.linter import PresetLinter
from src.platform.plugins.loader import PluginLoader


def _write_preset(tmp_path, preset_id, requirements_yaml):
    preset_dir = tmp_path / "presets" / preset_id
    preset_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "preset.yml").write_text(
        f"""schema: 1
id: "{preset_id}"
name: "Test Preset"
version: "1.0.0"
category: "image"
engine: "native"
requirements:
{requirements_yaml}
modes:
  - txt2img
"""
    )
    mode_dir = preset_dir / "modes" / "txt2img"
    mode_dir.mkdir(parents=True, exist_ok=True)
    (mode_dir / "pipeline.yml").write_text("pipeline: []\n")
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n")
    return preset_dir


class TestLintRequirements:
    def test_known_type_valid_args_has_no_requirements_error(self, tmp_path):
        _write_preset(
            tmp_path, "01AAAAAAAAAAAAAAAAAAAAAAAAA",
            "  - type: binary\n    name: ffmpeg\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        assert not any("requirements[" in i.message for i in issues)

    def test_unknown_type_is_error(self, tmp_path):
        _write_preset(
            tmp_path, "01BBBBBBBBBBBBBBBBBBBBBBBBB",
            "  - type: not_a_real_checker\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        matches = [i for i in issues if i.level == "error" and "unknown type" in i.message]
        assert len(matches) == 1
        assert "not_a_real_checker" in matches[0].message

    def test_bad_args_for_known_type_is_error(self, tmp_path):
        _write_preset(
            tmp_path, "01CCCCCCCCCCCCCCCCCCCCCCCCC",
            "  - type: vram_min_gb\n    gb: -5\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        matches = [i for i in issues if i.level == "error" and "requirements[0] (vram_min_gb)" in i.message]
        assert len(matches) == 1

    def test_missing_required_arg_is_error(self, tmp_path):
        _write_preset(
            tmp_path, "01DDDDDDDDDDDDDDDDDDDDDDDDD",
            "  - type: python_package\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        matches = [i for i in issues if i.level == "error" and "requirements[0] (python_package)" in i.message]
        assert len(matches) == 1

    def test_model_requirement_needs_exactly_one_of_tag_or_hash(self, tmp_path):
        _write_preset(
            tmp_path, "01EEEEEEEEEEEEEEEEEEEEEEEEE",
            "  - type: model\n",
        )
        issues = PresetLinter([str(tmp_path)]).lint()
        matches = [i for i in issues if i.level == "error" and "requirements[0] (model)" in i.message]
        assert len(matches) == 1


def _write_plugin(tmp_path, plugin_id, backend_ref, checker_source):
    """A tmp marketplace plugin declaring one `requirement_checkers:` entry,
    discovered via `PluginLoader` exactly like `scripts/preset_lint.py` does
    (no app boot, no enable)."""
    marketplace_dir = tmp_path / "plugins" / "marketplace"
    plugin_dir = marketplace_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "description": "Test plugin",
        "author": "Test Author",
        "type": "full-stack",
        "requirement_checkers": [{"type": "fixture_check", "backend": backend_ref}],
    }
    (plugin_dir / "manifest.yml").write_text(yaml.dump(manifest))
    (plugin_dir / "checkers.py").write_text(checker_source)
    local_dir = tmp_path / "plugins" / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    return PluginLoader(str(marketplace_dir), str(local_dir)).discover_plugins()


_FIXTURE_CHECKER_SOURCE = (
    "from pydantic import BaseModel, ConfigDict\n\n"
    "class FixtureCheckSchema(BaseModel):\n"
    "    model_config = ConfigDict(extra='forbid')\n"
    "    type: str\n"
    "    foo: str\n"
    "    hint: str | None = None\n"
    "    optional: bool = False\n\n"
    "class FixtureChecker:\n"
    "    type = 'fixture_check'\n"
    "    schema = FixtureCheckSchema\n\n"
    "    async def check(self, spec, ctx):\n"
    "        raise NotImplementedError\n"
)


class TestLintRequirementsPluginCheckers:
    """`_requirement_checker_registry` must resolve a plugin-declared
    `requirement_checkers:` type from its manifest alone (no app boot, no
    enable) - the gap a standalone `scripts/preset_lint.py` run used to hit
    for every ComfyUI-checker preset."""

    def test_plugin_checker_type_is_known_to_standalone_lint(self, tmp_path):
        plugin_manifests = _write_plugin(
            tmp_path, "fixture-checker-plugin", "checkers:FixtureChecker", _FIXTURE_CHECKER_SOURCE,
        )
        _write_preset(
            tmp_path, "01FFFFFFFFFFFFFFFFFFFFFFFFF",
            "  - type: fixture_check\n    foo: bar\n",
        )

        issues = PresetLinter([str(tmp_path)], plugin_manifests=plugin_manifests).lint()

        assert not any("requirements[" in i.message for i in issues)

    def test_plugin_checker_validates_its_own_schema(self, tmp_path):
        plugin_manifests = _write_plugin(
            tmp_path, "fixture-checker-plugin-2", "checkers:FixtureChecker", _FIXTURE_CHECKER_SOURCE,
        )
        _write_preset(
            tmp_path, "01GGGGGGGGGGGGGGGGGGGGGGGGG",
            "  - type: fixture_check\n",  # missing required 'foo'
        )

        issues = PresetLinter([str(tmp_path)], plugin_manifests=plugin_manifests).lint()

        matches = [i for i in issues if i.level == "error" and "requirements[0] (fixture_check)" in i.message]
        assert len(matches) == 1

    def test_unimportable_plugin_checker_is_warning_not_error(self, tmp_path):
        plugin_manifests = _write_plugin(
            tmp_path, "fixture-checker-plugin-3", "checkers:DoesNotExist", _FIXTURE_CHECKER_SOURCE,
        )
        _write_preset(
            tmp_path, "01HHHHHHHHHHHHHHHHHHHHHHHHH",
            "  - type: fixture_check\n    anything: goes\n",
        )

        issues = PresetLinter([str(tmp_path)], plugin_manifests=plugin_manifests).lint()

        assert not any(i.level == "error" and "requirements[" in i.message for i in issues)
        warnings = [i for i in issues if i.level == "warning" and "fixture-checker-plugin-3" in i.preset_path]
        assert len(warnings) == 1
        assert "could not import checker backend" in warnings[0].message
