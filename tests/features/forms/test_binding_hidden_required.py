from __future__ import annotations

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate

HIDE_WHEN_SKIP = {"when": {"field": "skip", "operator": "equals", "value": True}, "then": {"set_visibility": False}}


def _preset(fields):
    forms = [FormTemplate(name="custom", fields=fields, default=True, order=0)]
    return PresetTemplate(
        id="preset_1",
        name="Preset One",
        version="1.0.0",
        path="/presets/preset_1",
        modes={"txt2img": ModeTemplate(forms=forms, pipes=[])},
    )


def _skip_field():
    return FieldTemplate(type="checkbox", name="skip", default=False)


def _model_field(**kwargs):
    return FieldTemplate(type="model", name="model", required=True, **kwargs)


def _bind(preset, data):
    return bind_form(preset, "txt2img", None, data, "user_1")


def _field_errors(preset, data):
    with pytest.raises(FormBindingError) as exc:
        _bind(preset, data)
    return exc.value.field_errors


def test_required_field_hidden_by_its_own_reaction_is_not_required():
    preset = _preset([_skip_field(), _model_field(reactions=[HIDE_WHEN_SKIP])])

    bound = _bind(preset, {"skip": True, "model": ""})

    assert bound.values["model"] == ""


def test_required_field_shown_by_its_reaction_is_still_required():
    preset = _preset([_skip_field(), _model_field(reactions=[HIDE_WHEN_SKIP])])

    assert "model" in _field_errors(preset, {"skip": False, "model": ""})


def test_later_matching_reaction_without_visibility_keeps_the_field_hidden():
    set_value_only = {
        "when": {"field": "skip", "operator": "equals", "value": True},
        "then": {"set_visibility": None, "set_value": "auto"},
    }
    preset = _preset([_skip_field(), _model_field(reactions=[HIDE_WHEN_SKIP, set_value_only])])

    bound = _bind(preset, {"skip": True, "model": ""})

    assert bound.values["model"] == ""


def test_required_field_inside_a_hidden_section_is_not_required():
    section = FieldTemplate(type="section", label="Models", reactions=[HIDE_WHEN_SKIP], children=[_model_field()])
    preset = _preset([_skip_field(), section])

    bound = _bind(preset, {"skip": True})

    assert bound.values["model"] is None


def test_required_field_inside_a_visible_section_is_still_required():
    section = FieldTemplate(type="section", label="Models", reactions=[HIDE_WHEN_SKIP], children=[_model_field()])
    preset = _preset([_skip_field(), section])

    assert "model" in _field_errors(preset, {"skip": False})


def test_required_field_inside_a_statically_hidden_section_is_not_required():
    section = FieldTemplate(type="section", label="Models", visible=False, children=[_model_field()])
    preset = _preset([section])

    bound = _bind(preset, {})

    assert bound.values["model"] is None


def _image_field(required_when_skip):
    return FieldTemplate(
        type="image",
        name="image",
        reactions=[
            {
                "when": {"field": "skip", "operator": "equals", "value": required_when_skip},
                "then": {"update_validation": {"required": True, "mask_required": True}},
            },
            HIDE_WHEN_SKIP,
        ],
    )


def test_visible_field_keeps_reaction_required_and_mask_required():
    preset = _preset([_skip_field(), _image_field(False)])

    assert _field_errors(preset, {"skip": False, "image": ""})["image"] == [
        "required field is missing",
        "paint a mask on this image",
    ]


def test_hidden_field_skips_reaction_required_and_mask_required():
    preset = _preset([_skip_field(), _image_field(True)])

    bound = _bind(preset, {"skip": True, "image": ""})

    assert not bound.values["image"]


def test_hidden_field_still_checks_its_numeric_range():
    slider = FieldTemplate(
        type="slider", name="steps", default=4, configuration={"min": 1, "max": 10}, reactions=[HIDE_WHEN_SKIP]
    )
    preset = _preset([_skip_field(), slider])

    assert "steps" in _field_errors(preset, {"skip": True, "steps": 99})


def test_logical_group_hides_a_section_only_when_every_condition_holds():
    section = FieldTemplate(
        type="section",
        label="Models",
        reactions=[
            {
                "when": {
                    "logic": "AND",
                    "conditions": [
                        {"field": "skip", "operator": "equals", "value": True},
                        {"field": "kind", "operator": "not_in", "value": ["none"]},
                    ],
                },
                "then": {"set_visibility": False},
            }
        ],
        children=[_model_field()],
    )
    preset = _preset([_skip_field(), FieldTemplate(type="string", name="kind", default="edge"), section])

    assert _bind(preset, {"skip": True, "kind": "edge"}).values["model"] is None
    assert "model" in _field_errors(preset, {"skip": True, "kind": "none"})


def _task_field():
    return FieldTemplate(type="string", name="task", default="a")


def _lora_field(task):
    return FieldTemplate(
        type="model",
        name=f"{task}_lora",
        required=True,
        reactions=[
            {"when": {"field": "task", "operator": "not_equals", "value": task}, "then": {"set_required": False}},
            {"when": {"field": "task", "operator": "equals", "value": task}, "then": {"set_required": True}},
        ],
    )


def _task_preset():
    return _preset([_task_field(), _lora_field("a"), _lora_field("b")])


def test_set_required_false_waives_a_statically_required_field():
    bound = _bind(_task_preset(), {"task": "a", "a_lora": "x.safetensors", "b_lora": ""})

    assert bound.values["b_lora"] == ""


def test_set_required_true_requires_the_selected_tasks_field_and_names_it():
    errors = _field_errors(_task_preset(), {"task": "b", "a_lora": "x.safetensors", "b_lora": ""})

    assert list(errors) == ["b_lora"]


def test_set_required_true_makes_an_optional_field_required():
    optional = FieldTemplate(
        type="model",
        name="opt",
        reactions=[{"when": {"field": "task", "operator": "equals", "value": "a"}, "then": {"set_required": True}}],
    )
    preset = _preset([_task_field(), optional])

    assert "opt" in _field_errors(preset, {"task": "a", "opt": ""})
    assert _bind(preset, {"task": "b", "opt": ""}).values["opt"] == ""


def test_a_hidden_field_stays_waived_even_when_set_required_is_true():
    field = FieldTemplate(
        type="model",
        name="opt",
        reactions=[
            {"when": {"field": "task", "operator": "equals", "value": "a"}, "then": {"set_required": True}},
            {"when": {"field": "task", "operator": "equals", "value": "a"}, "then": {"set_visibility": False}},
        ],
    )
    preset = _preset([_task_field(), field])

    assert _bind(preset, {"task": "a", "opt": ""}).values["opt"] == ""


def test_the_reaction_schema_accepts_set_required():
    from src.features.presets.schema import ReactionSpec

    spec = ReactionSpec.model_validate({"when": {"field": "a", "equals": 1}, "then": {"set_required": True}})

    assert spec.then.set_required is True
