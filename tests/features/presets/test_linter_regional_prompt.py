import pytest

from src.features.presets.linter import PresetLinter
from tests.features.presets.test_linter import _write_preset

PIPELINE_WITH_STAGE = (
    "pipeline:\n"
    "  - name: regional_prompt/moss_image\n"
    "    configuration: {}\n"
    "  - name: gallery\n"
    "    input:\n      - [image, generator, image]\n"
)
RESOLUTION_FORM = "name: custom\nfields:\n  - name: resolution\n    type: resolution\n    label: Resolution\n"


def _lint(tmp_path, vars_yaml, pipeline=PIPELINE_WITH_STAGE, form=RESOLUTION_FORM, modes=("txt2img",)):
    preset_dir = _write_preset(tmp_path, "presets/native/Foo/std", "01RAAAAAAAAAAAAAAAAAAAAAAAA", list(modes), extra_yaml=vars_yaml)
    for mode in modes:
        mode_dir = preset_dir / "modes" / mode
        if pipeline is not None:
            (mode_dir / "pipeline.yml").write_text(pipeline)
        if form is not None:
            (mode_dir / "form.yml").write_text(form)
    return [i for i in PresetLinter([str(tmp_path)]).lint() if "regional_prompt" in i.message]


def _declaration(modes="[txt2img]", size_field="resolution"):
    return f"vars:\n  regional_prompt:\n    modes: {modes}\n    size_field: {size_field}\n"


def test_valid_declaration_has_no_regional_issues(tmp_path):
    assert _lint(tmp_path, _declaration()) == []


def test_preset_without_the_option_is_not_checked(tmp_path):
    assert _lint(tmp_path, "", pipeline=None) == []


def test_unknown_mode_is_an_error(tmp_path):
    issues = _lint(tmp_path, _declaration(modes="[txt2img, ghost]"))
    assert [i.level for i in issues] == ["error"]
    assert "'ghost' is not a mode of this preset" in issues[0].message


def test_missing_stage_is_an_error(tmp_path):
    pipeline = "pipeline:\n  - name: gallery\n    input:\n      - [image, generator, image]\n"
    issues = _lint(tmp_path, _declaration(), pipeline=pipeline)
    assert [i.level for i in issues] == ["error"]
    assert "no regional_prompt/* stage" in issues[0].message


@pytest.mark.parametrize("form", [
    "name: custom\nfields:\n  - name: resolution\n    type: string\n    label: Resolution\n",
    "name: custom\nfields:\n  - name: other\n    type: resolution\n    label: Other\n",
])
def test_size_field_that_is_not_a_resolution_field_is_an_error(tmp_path, form):
    issues = _lint(tmp_path, _declaration(), form=form)
    assert [i.level for i in issues] == ["error"]
    assert "size_field 'resolution' is not a resolution field" in issues[0].message


def test_malformed_declaration_is_an_error(tmp_path):
    issues = _lint(tmp_path, "vars:\n  regional_prompt: nope\n")
    assert [i.level for i in issues] == ["error"]
    assert "must be a mapping" in issues[0].message


def test_empty_mode_list_is_an_error(tmp_path):
    issues = _lint(tmp_path, _declaration(modes="[]"))
    assert [i.level for i in issues] == ["error"]
