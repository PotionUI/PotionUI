from types import SimpleNamespace

from src.features.generation.parameter_meta import ParamMetaStamp, form_field_meta
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate
from src.pipelines.outputs import GenerationOutput, ParamGenerationOutput


def _template(forms, mode="txt2img"):
    return SimpleNamespace(modes={mode: ModeTemplate(forms=forms, pipes=[])})


def _form(name, fields, default=False):
    return FormTemplate(name=name, fields=fields, default=default)


def test_label_and_type_come_from_the_matching_form_field():
    template = _template([_form("main", [
        FieldTemplate(type="number", name="duration", label="Length"),
        FieldTemplate(type="text", name="note"),
    ])])

    meta = form_field_meta(template, "txt2img")

    assert meta["duration"] == {"label": "Length", "type": "number"}
    assert meta["note"] == {"type": "text"}
    assert all("unit" not in entry for entry in meta.values())


def test_nested_children_are_searched_and_the_first_field_of_a_name_wins():
    template = _template([_form("main", [
        FieldTemplate(type="group", name="box", label="Box", children=[
            FieldTemplate(type="slider", name="strength", label="Strength"),
            {"@loop": {"items": "x"}},
        ]),
        FieldTemplate(type="number", name="strength", label="Other"),
    ])])

    assert form_field_meta(template, "txt2img")["strength"]["label"] == "Strength"


def test_the_named_form_variant_is_used_then_the_default_one():
    template = _template([
        _form("a", [FieldTemplate(type="number", name="x", label="From A")]),
        _form("b", [FieldTemplate(type="number", name="x", label="From B")], default=True),
    ])

    assert form_field_meta(template, "txt2img", "a")["x"]["label"] == "From A"
    assert form_field_meta(template, "txt2img")["x"]["label"] == "From B"


def test_unknown_modes_and_empty_forms_give_nothing():
    assert form_field_meta(_template([]), "txt2img") == {}
    assert form_field_meta(_template([_form("m", [])]), "other") == {}
    assert ParamMetaStamp.plan(_template([]), "txt2img") is None


def test_the_stamp_fills_only_outputs_without_their_own_meta():
    template = _template([_form("m", [FieldTemplate(type="number", name="duration", label="Length")])])
    stamp = ParamMetaStamp.plan(template, "txt2img")
    plain = ParamGenerationOutput(name="duration", values=[5])
    own = ParamGenerationOutput(name="duration", values=[5], meta={"label": "Mine"})
    other = ParamGenerationOutput(name="seed", values=[1])

    for output in (plain, own, other):
        stamp.stamp(output)

    assert plain.meta == {"label": "Length", "type": "number"}
    assert own.meta == {"label": "Mine"}
    assert other.meta is None


def test_the_stamp_ignores_other_outputs():
    template = _template([_form("m", [FieldTemplate(type="number", name="duration", label="Length")])])
    stamp = ParamMetaStamp.plan(template, "txt2img")

    stamp.stamp(GenerationOutput())


def test_a_template_without_real_modes_or_forms_gives_nothing():
    from unittest.mock import Mock

    assert ParamMetaStamp.plan(Mock(), "txt2img") is None
    assert ParamMetaStamp.plan(SimpleNamespace(modes={"txt2img": Mock()}), "txt2img") is None
