from __future__ import annotations

from pathlib import Path

import pytest

from src.features.forms.binding import FormBindingError, _expand_form_fields, bind_form
from src.features.presets import PresetTemplateLoader

MODEL_FIELDS = ("diffusion_model", "text_encoder", "vae", "control_model")


@pytest.fixture(scope="module")
def qwen21():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    matches = [p for p in loader.presets if Path(p.path).name == "QwenImage-2.1"]
    assert len(matches) == 1
    return matches[0]


def _wire(**overrides):
    data = {"source_image": "uploads/photo.png", "guide": "openpose", "guide_extract": True, "guide_only": True}
    data.update({name: "" for name in MODEL_FIELDS})
    data.update(overrides)
    return data


def _model_errors(preset, data):
    with pytest.raises(FormBindingError) as exc:
        bind_form(preset, "control", None, data, "user_1")
    return {name for name in MODEL_FIELDS if name in exc.value.field_errors}


def test_guide_only_submit_without_models_passes(qwen21):
    bound = bind_form(qwen21, "control", None, _wire(), "user_1")

    assert bound.values["guide_only"] is True
    assert all(bound.values[name] == "" for name in MODEL_FIELDS)


def test_guide_only_submit_with_model_fields_absent_passes(qwen21):
    data = {k: v for k, v in _wire().items() if k not in MODEL_FIELDS}

    bound = bind_form(qwen21, "control", None, data, "user_1")

    assert bound.values["guide"] == "openpose"


def test_control_without_guide_only_still_requires_every_model(qwen21):
    assert _model_errors(qwen21, _wire(guide_only=False)) == set(MODEL_FIELDS)


@pytest.mark.parametrize(
    "overrides",
    [{"guide_extract": False}, {"guide": "none"}, {"guide": "grayscale"}],
)
def test_guide_only_left_on_without_an_extracted_guide_still_requires_models(qwen21, overrides):
    assert _model_errors(qwen21, _wire(**overrides)) == set(MODEL_FIELDS)


def _sugar_triples(conditions):
    triples = []
    for condition in conditions:
        operator = next(key for key in condition if key != "field")
        triples.append((condition["field"], operator, condition[operator]))
    return triples


def _models_section(fields):
    for field in fields or []:
        if field.type == "section" and any(child.name == "diffusion_model" for child in field.children or []):
            return field
        if isinstance(field.children, list):
            found = _models_section(field.children)
            if found is not None:
                return found
    return None


def test_models_section_hides_on_the_same_condition_that_makes_the_mode_promptless(qwen21):
    fields = _expand_form_fields(qwen21.modes["control"].forms[0].fields, qwen21)
    section = _models_section(fields)
    hide = [r for r in section.reactions if r["then"].get("set_visibility") is False]
    promptless = next(entry for entry in qwen21.vars["promptless_modes"] if entry["mode"] == "control")

    assert len(hide) == 1
    assert hide[0]["when"]["logic"] == promptless["when"]["logic"] == "AND"
    assert [(c["field"], c["operator"], c["value"]) for c in hide[0]["when"]["conditions"]] == _sugar_triples(
        promptless["when"]["conditions"]
    )
