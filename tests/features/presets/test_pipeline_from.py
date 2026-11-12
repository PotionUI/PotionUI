from __future__ import annotations

import pytest

from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.pipeline_source import load_mode_pipeline, pointer_problems, resolve_mode_pipeline_file

PIPELINE = "pipeline:\n  - name: gallery\n    enabled: true\n"
FORM = "name: custom\nfields:\n  - name: seed\n    type: seed\n    default: -1\n"


def write_preset(root, modes, extra_modes=None):
    preset_dir = root / "presets" / "native" / "Foo" / "std"
    preset_dir.mkdir(parents=True)
    names = "\n".join(f"  - {name}" for name in modes)
    (preset_dir / "preset.yml").write_text(
        f'schema: 1\nid: "01PIPELINEFROMAAAAAAAAAAAAA"\nname: "Test"\nversion: "1.0.0"\n'
        f'category: "video"\nengine: "native"\nmodes:\n{names}\n',
        encoding="utf-8",
    )
    (preset_dir / "tests.yml").write_text("schema: 1\ncases: []\n", encoding="utf-8")
    for name, pipeline in {**{m: PIPELINE for m in modes}, **(extra_modes or {})}.items():
        mode_dir = preset_dir / "modes" / name
        mode_dir.mkdir(parents=True, exist_ok=True)
        (mode_dir / "pipeline.yml").write_text(pipeline, encoding="utf-8")
        (mode_dir / "form.yml").write_text(FORM, encoding="utf-8")
    return preset_dir


def test_a_pointer_resolves_to_the_siblings_pipeline_file(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"b": "pipeline_from: a\n"})
    assert resolve_mode_pipeline_file(preset / "modes" / "b") == preset / "modes" / "a" / "pipeline.yml"
    assert resolve_mode_pipeline_file(preset / "modes" / "a") == preset / "modes" / "a" / "pipeline.yml"


def test_the_loader_gives_the_pointing_mode_the_targets_pipes(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"b": "pipeline_from: a\n"})
    mode, errors = PresetTemplateLoader([str(tmp_path)])._load_mode(preset / "preset.yml", "b")
    assert errors == []
    assert [pipe.name for pipe in mode.pipes] == ["gallery"]


@pytest.mark.parametrize("pointer,fragment", [
    ("pipeline_from: missing\n", "not found"),
    ("pipeline_from: b\n", "point at itself"),
    ("pipeline_from: c\n", "itself a pointer"),
    ("pipeline_from: ../a\n", "sibling mode"),
    ("pipeline_from: 3\n", "sibling mode"),
    ("pipeline_from: a\npipeline: []\n", "cannot be combined"),
])
def test_a_bad_pointer_is_reported(tmp_path, pointer, fragment):
    preset = write_preset(tmp_path, ["a", "b", "c"], {"b": pointer, "c": "pipeline_from: a\n"})
    problems = pointer_problems(preset / "modes" / "b")
    assert len(problems) == 1 and fragment in problems[0]
    data, errors = load_mode_pipeline(preset / "modes" / "b")
    assert data is None and errors == problems


def test_the_loader_refuses_a_mode_with_a_bad_pointer(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"b": "pipeline_from: missing\n"})
    mode, errors = PresetTemplateLoader([str(tmp_path)])._load_mode(preset / "preset.yml", "b")
    assert mode is None
    assert any("modes/b/pipeline.yml" in error and "not found" in error for error in errors)


def test_two_pointers_that_point_at_each_other_are_both_errors(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"a": "pipeline_from: b\n", "b": "pipeline_from: a\n"})
    assert pointer_problems(preset / "modes" / "a") and pointer_problems(preset / "modes" / "b")


def test_the_linter_reports_a_bad_pointer_and_accepts_a_good_one(tmp_path):
    good = tmp_path / "good"
    write_preset(good, ["a", "b"], {"b": "pipeline_from: a\n"})
    assert [i for i in PresetLinter([str(good)]).lint() if i.level == "error"] == []

    bad = tmp_path / "bad"
    write_preset(bad, ["a", "b"], {"b": "pipeline_from: nowhere\n"})
    errors = [i for i in PresetLinter([str(bad)]).lint() if i.level == "error"]
    assert any("modes/b/pipeline.yml" in i.message and "not found" in i.message for i in errors)


def test_a_yaml_error_in_a_pointer_file_is_a_problem_and_never_taken_for_no_pointer(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"b": "pipeline_from: [unclosed\n"})
    assert any("failed to parse" in problem for problem in pointer_problems(preset / "modes" / "b"))
    data, errors = load_mode_pipeline(preset / "modes" / "b")
    assert data is None and errors
    mode, load_errors = PresetTemplateLoader([str(tmp_path)])._load_mode(preset / "preset.yml", "b")
    assert mode is None and load_errors


def test_a_yaml_error_in_the_target_is_reported_on_the_pointing_mode(tmp_path):
    preset = write_preset(tmp_path, ["a", "b"], {"a": "pipeline: [unclosed\n", "b": "pipeline_from: a\n"})
    assert any("failed to parse" in problem for problem in pointer_problems(preset / "modes" / "b"))
