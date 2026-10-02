import pytest

from src.features.forms.binding import bind_form
from src.features.presets.form_overrides import (
    apply_overrides_to_fields,
    build_inventory_entries,
    validate_form_overrides,
)
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate

TEMPLATE = "{{ preset.vars.default_fps }}"


def _fps(default=25):
    return FieldTemplate(type="slider", name="fps", default=default, configuration={"min": 1, "max": 60})


def _preset(vars_=None):
    form = FormTemplate(name="custom", fields=[_fps()], default=True, order=0)
    return PresetTemplate(
        id="p", name="P", version="1", path="/p",
        modes={"video": ModeTemplate(forms=[form], pipes=[])}, vars=vars_ or {},
    )


def _default_after(overrides, vars_=None):
    preset = _preset(vars_)
    fields = apply_overrides_to_fields(preset.modes["video"].forms[0].fields, overrides, preset)
    return fields[0].default


def test_template_override_default_resolves_to_the_preset_var():
    assert _default_after({"fps": {"default": TEMPLATE}}, {"default_fps": 30}) == 30


def test_unresolvable_template_override_keeps_the_declared_default():
    assert _default_after({"fps": {"default": TEMPLATE}}, {}) == 25


def test_invalid_override_default_keeps_the_declared_default():
    assert _default_after({"fps": {"default": "fast"}}) == 25
    assert _default_after({"fps": {"default": 999}}) == 25


def test_valid_numeric_override_still_applies():
    assert _default_after({"fps": {"default": 30}}) == 30


def test_bind_ignores_a_template_override_default_that_does_not_resolve():
    bound = bind_form(_preset(), "video", None, {}, "u", field_overrides={"fps": {"default": TEMPLATE}})
    assert bound.values["fps"] == 25


def test_bind_resolves_a_template_override_default_from_preset_vars():
    bound = bind_form(_preset({"default_fps": 30}), "video", None, {}, "u", field_overrides={"fps": {"default": TEMPLATE}})
    assert bound.values["fps"] == 30


def test_locked_field_with_bad_override_default_binds_the_declared_default():
    bound = bind_form(
        _preset(), "video", None, {"fps": 40}, "u",
        field_overrides={"fps": {"default": TEMPLATE, "editable": False}},
    )
    assert bound.values["fps"] == 25


def test_saving_a_template_that_does_not_resolve_is_refused_plainly():
    errors = validate_form_overrides(_preset(), "video", {"fps": {"default": TEMPLATE}})
    assert len(errors) == 1
    assert "does not resolve" in errors[0] and "plain number" in errors[0]


def test_saving_a_template_that_resolves_is_accepted():
    assert validate_form_overrides(_preset({"default_fps": 30}), "video", {"fps": {"default": TEMPLATE}}) == []


def _single_field_preset(field, vars_=None):
    form = FormTemplate(name="custom", fields=[field], default=True, order=0)
    return PresetTemplate(
        id="p", name="P", version="1", path="/p",
        modes={"video": ModeTemplate(forms=[form], pipes=[])}, vars=vars_ or {},
    )


def _text(default="hello"):
    return FieldTemplate(type="text", name="note", default=default, required=True)


def _select():
    return FieldTemplate(
        type="select", name="mode_pick", default="a",
        configuration={"options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}]},
    )


def _seed():
    return FieldTemplate(type="seed", name="seed", default=7, configuration={"min": 0, "max": 100})


def _integer():
    return FieldTemplate(type="integer", name="count", default=2, configuration={"min": 1, "max": 9})


@pytest.mark.parametrize(
    "field, bad_default, declared",
    [
        (_text(), "", "hello"),
        (_select(), "gone", "a"),
        (_fps(), 999, 25),
        (_fps(), TEMPLATE, 25),
        (_fps(), "{{a}} x {{b}}", 25),
        (_seed(), "{{ preset.vars.nope }}", 7),
        (_integer(), "{{ preset.vars.nope }}", 2),
    ],
    ids=["required-text-empty", "removed-option", "out-of-range", "unresolved-template", "mid-string-marker", "seed-marker", "integer-marker"],
)
def test_unusable_override_default_is_ignored_and_reported(field, bad_default, declared):
    preset = _single_field_preset(field)
    fields = apply_overrides_to_fields(preset.modes["video"].forms[0].fields, {field.name: {"default": bad_default}}, preset)
    assert fields[0].default == declared
    entries, _tabs = build_inventory_entries(preset, "video", {field.name: {"default": bad_default}})
    assert entries[0]["default_ignored_reason"]


@pytest.mark.parametrize(
    "field, good_default",
    [(_text(), "changed"), (_select(), "b"), (_fps(), 30), (_seed(), 50), (_integer(), 5)],
    ids=["text", "select", "slider", "seed", "integer"],
)
def test_usable_override_default_is_not_reported(field, good_default):
    preset = _single_field_preset(field)
    entries, _tabs = build_inventory_entries(preset, "video", {field.name: {"default": good_default}})
    assert entries[0]["default_ignored_reason"] is None


@pytest.mark.parametrize("field", [_seed(), _integer()], ids=["seed", "integer"])
def test_numeric_types_resolve_a_preset_var_marker(field):
    preset = _single_field_preset(field, {"v": 4})
    fields = apply_overrides_to_fields(
        preset.modes["video"].forms[0].fields, {field.name: {"default": "{{ preset.vars.v }}"}}, preset
    )
    assert fields[0].default == 4


def test_override_inside_a_container_resolves_against_the_preset_vars():
    group = FieldTemplate(type="group", name="g", children=[_fps()])
    preset = _single_field_preset(group, {"default_fps": 30})
    fields = apply_overrides_to_fields(preset.modes["video"].forms[0].fields, {"fps": {"default": TEMPLATE}}, preset)
    assert fields[0].children[0].default == 30


def test_override_without_a_default_is_never_reported():
    preset = _single_field_preset(_fps())
    entries, _tabs = build_inventory_entries(preset, "video", {"fps": {"editable": False}})
    assert entries[0]["default_ignored_reason"] is None
