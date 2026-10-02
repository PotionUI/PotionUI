import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate


def _preset(vars_, field_type="slider", default=25):
    field = FieldTemplate(
        type=field_type, name="fps", default=default, configuration={"min": 1, "max": 60}
    )
    text = FieldTemplate(type="string", name="prompt", default="")
    form = FormTemplate(name="custom", fields=[field, text], default=True, order=0)
    return PresetTemplate(
        id="p", name="P", version="1", path="/p",
        modes={"video": ModeTemplate(forms=[form], pipes=[])}, vars=vars_,
    )


def _bind(preset, raw):
    return bind_form(preset, "video", None, raw, "u")


def test_preset_var_reference_resolves_to_the_var():
    bound = _bind(_preset({"default_fps": 24}), {"fps": "{{ preset.vars.default_fps }}"})
    assert bound.values["fps"] == 24


def test_unknown_var_falls_back_to_the_field_default():
    bound = _bind(_preset({}), {"fps": "{{ preset.vars.default_fps }}"})
    assert bound.values["fps"] == 25


def test_non_numeric_var_falls_back_to_the_field_default():
    bound = _bind(_preset({"default_fps": "fast"}), {"fps": "{{ preset.vars.default_fps }}"})
    assert bound.values["fps"] == 25


def test_other_template_expression_falls_back_to_the_field_default():
    bound = _bind(_preset({}), {"fps": "{{ form.x }}"})
    assert bound.values["fps"] == 25


def test_out_of_range_var_is_still_rejected():
    with pytest.raises(FormBindingError):
        _bind(_preset({"default_fps": 999}), {"fps": "{{ preset.vars.default_fps }}"})


def test_text_fields_keep_template_looking_content():
    bound = _bind(_preset({}), {"fps": 30, "prompt": "{{ preset.vars.default_fps }}"})
    assert bound.values["prompt"] == "{{ preset.vars.default_fps }}"
