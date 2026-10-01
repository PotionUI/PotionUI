from types import SimpleNamespace

import pytest

from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.loader import PresetTemplateLoader
from src.plugin_api import presets as presets_api
from src.plugin_api.presets import FieldDescription, describe_preset

PRESET_ID = "01TESTDESCRIBEPRESET000000"

PRESET_YML = f"""schema: 1
id: "{PRESET_ID}"
name: "Describe Me"
version: "1.0.0"
category: "image"
engine: "native"
vars:
  fps: 24
  capabilities:
    modes: ["edit"]
modes:
  - edit
  - txt2img
"""

EDIT_FORM = """name: "simple"
fields:
  - type: "tabs"
    children:
      - type: "tab"
        label: "Inputs"
        children:
          - name: "source_image"
            type: "image"
            label: "Source"
            required: true
          - name: "references"
            type: "image"
            label: "References"
            configuration: {multi: true, max_items: 4}
          - type: "row"
            children:
              - name: "seed"
                type: "seed"
                label: "Seed"
"""

EDIT_VARIANT = """name: "detailed"
default: true
fields:
  - name: "source_image"
    type: "image"
    label: "Source"
  - name: "strength"
    type: "slider"
    label: "Strength"
    configuration: {min: 0, max: 1, step: 0.1}
"""

TXT2IMG_FORM = """name: "custom"
fields:
  - name: "quantity"
    type: "stepper"
    label: "Quantity"
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def described(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    preset = root / "DescribeMe"
    _write(preset / "preset.yml", PRESET_YML)
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    _write(preset / "modes" / "edit" / "form.yml", EDIT_FORM)
    _write(preset / "modes" / "edit" / "pipeline.yml", "pipeline: []\n")
    _write(preset / "modes" / "edit" / "variants" / "detailed" / "form.yml", EDIT_VARIANT)
    _write(preset / "modes" / "txt2img" / "form.yml", TXT2IMG_FORM)
    _write(preset / "modes" / "txt2img" / "pipeline.yml", "pipeline: []\n")
    loader = PresetTemplateLoader([str(root)])
    repository = FilePresetRepository(loader)
    monkeypatch.setattr(presets_api, "get_container", lambda: SimpleNamespace(file_preset_repository=repository))
    return loader


def test_describes_modes_forms_and_nested_fields(described):
    description = describe_preset(PRESET_ID)

    assert described.load_errors == {}
    assert description.id == PRESET_ID
    assert description.name == "Describe Me"
    assert set(description.modes) == {"edit", "txt2img"}
    edit = description.modes["edit"]
    assert set(edit.forms) == {"simple", "detailed"}
    assert edit.forms["simple"] == {
        "source_image": FieldDescription(type="image", required=True),
        "references": FieldDescription(type="image", required=False, multi=True),
        "seed": FieldDescription(type="seed", required=False),
    }
    assert edit.forms["simple"]["source_image"].multi is False
    assert edit.forms["detailed"]["source_image"] == FieldDescription(type="image", required=False)
    assert edit.forms["detailed"]["strength"].type == "slider"


def test_the_default_form_follows_the_variant_rule(described):
    description = describe_preset(PRESET_ID)

    assert description.modes["edit"].default_form == "detailed"
    assert description.modes["txt2img"].default_form == "custom"


def test_vars_are_a_copy(described):
    first = describe_preset(PRESET_ID)
    first.vars["capabilities"]["modes"].append("changed")

    assert describe_preset(PRESET_ID).vars == {"fps": 24, "capabilities": {"modes": ["edit"]}}


def test_an_unknown_preset_is_none(described):
    assert describe_preset("NOT-INSTALLED") is None


def test_exported_from_the_package_root():
    import src.plugin_api as plugin_api

    for name in ("describe_preset", "PresetDescription", "ModeDescription", "FieldDescription"):
        assert name in plugin_api.__all__
        assert getattr(plugin_api, name) is getattr(presets_api, name)
