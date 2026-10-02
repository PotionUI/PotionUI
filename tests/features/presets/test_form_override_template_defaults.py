from src.features.forms.binding import bind_form
from src.features.presets.form_overrides import apply_overrides_to_fields, validate_form_overrides
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
