"""PresetLinter's `requirements:` checks: unknown type and per-type schema
validation - see `PresetLinter._lint_requirements`.
"""

from src.features.presets.linter import PresetLinter


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
