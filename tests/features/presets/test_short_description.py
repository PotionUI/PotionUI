from types import SimpleNamespace

import pytest

from src.features.presets import operations
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader

PRESET_ID = "01TESTSHORTDESCRIPTION0000"

PRESET_YML = f"""schema: 1
id: "{PRESET_ID}"
name: "Short"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
  - edit
"""

FORM_WITH = """name: "custom"
short_description: "Generate an image from a text prompt."
fields: []
"""

FORM_WITHOUT = """name: "custom"
fields: []
"""

VARIANT = """name: "alt"
default: true
short_description: "The alternative form."
fields: []
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _preset(root, txt2img_form, edit_form, edit_variant=None):
    preset = root / "Short"
    _write(preset / "preset.yml", PRESET_YML)
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    for mode, form in (("txt2img", txt2img_form), ("edit", edit_form)):
        _write(preset / "modes" / mode / "form.yml", form)
        _write(preset / "modes" / mode / "pipeline.yml", "pipeline: []\n")
    if edit_variant:
        _write(preset / "modes" / "edit" / "variants" / "alt" / "form.yml", edit_variant)
    return preset


@pytest.fixture
def served(tmp_path):
    _preset(tmp_path, FORM_WITH, FORM_WITHOUT, VARIANT)
    loader = PresetTemplateLoader([str(tmp_path)])
    collaborators = SimpleNamespace(file_repo=FilePresetRepository(loader))
    return loader, operations.get_available_modes(collaborators, PRESET_ID)


def test_loader_carries_short_description(served):
    loader, _ = served
    modes = FilePresetRepository(loader).find_preset_by_id(PRESET_ID).modes
    assert modes["txt2img"].forms[0].short_description == "Generate an image from a text prompt."
    assert sorted(f.short_description or "" for f in modes["edit"].forms) == ["", "The alternative form."]


def test_modes_response_serves_short_description(served):
    _, result = served
    by_name = {m["name"]: m for m in result["modes"]}
    assert by_name["txt2img"]["short_description"] == "Generate an image from a text prompt."
    assert by_name["txt2img"]["variants"][0]["short_description"] == "Generate an image from a text prompt."


def test_mode_uses_default_variant_value(served):
    _, result = served
    edit = next(m for m in result["modes"] if m["name"] == "edit")
    assert edit["short_description"] == "The alternative form."


def test_missing_short_description_is_none(tmp_path):
    _preset(tmp_path, FORM_WITHOUT, FORM_WITHOUT)
    collaborators = SimpleNamespace(file_repo=FilePresetRepository(PresetTemplateLoader([str(tmp_path)])))
    result = operations.get_available_modes(collaborators, PRESET_ID)
    assert all(m["short_description"] is None for m in result["modes"])


def test_lint_warns_over_100_characters(tmp_path):
    long_form = 'name: "custom"\nshort_description: "' + "x" * 101 + '"\nfields: []\n'
    _preset(tmp_path, long_form, FORM_WITH)
    issues = PresetLinter([str(tmp_path)]).lint()
    hits = [i for i in issues if "short_description" in i.message and i.level == "warning"]
    assert len(hits) == 1
    assert "modes/txt2img" in hits[0].message


def test_lint_accepts_100_characters(tmp_path):
    form = 'name: "custom"\nshort_description: "' + "x" * 100 + '"\nfields: []\n'
    _preset(tmp_path, form, FORM_WITH)
    issues = PresetLinter([str(tmp_path)]).lint()
    assert not any("short_description" in i.message for i in issues)


def test_lint_hints_when_a_multi_mode_preset_mode_has_no_description(tmp_path):
    _preset(tmp_path, FORM_WITH, FORM_WITHOUT)
    issues = PresetLinter([str(tmp_path)]).lint()
    hits = [i for i in issues if i.level == "info" and "short_description" in i.message]
    assert len(hits) == 1
    assert "modes/edit" in hits[0].message


def test_lint_no_hint_for_single_mode_preset(tmp_path):
    preset = tmp_path / "Solo"
    _write(preset / "preset.yml", PRESET_YML.replace("  - edit\n", ""))
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    _write(preset / "modes" / "txt2img" / "form.yml", FORM_WITHOUT)
    _write(preset / "modes" / "txt2img" / "pipeline.yml", "pipeline: []\n")
    issues = PresetLinter([str(tmp_path)]).lint()
    assert not any("short_description" in i.message for i in issues)
