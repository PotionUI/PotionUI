import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate

DOCUMENT = {"enabled": True, "items": [{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "caption": "../../etc/passwd"}]}


def _preset(vars_=None, modes=("txt2img",)):
    forms = [FormTemplate(name="custom", fields=[FieldTemplate(type="string", name="seed", default="1")], default=True, order=0)]
    return PresetTemplate(
        id="preset_1",
        name="Preset One",
        version="1.0.0",
        path="/presets/preset_1",
        modes={mode: ModeTemplate(forms=forms, pipes=[]) for mode in modes},
        vars=vars_,
    )


def _declared(modes):
    return {"regional_prompt": {"modes": modes, "size_field": "resolution"}}


def _bind(preset, mode="txt2img", **kwargs):
    return bind_form(preset, mode, None, {"regional_prompt": DOCUMENT}, "user_1", storage_dir="/nonexistent/storage", **kwargs)


def test_passes_through_for_a_declaring_preset_and_mode():
    bound = _bind(_preset(_declared(["txt2img"])))
    assert bound.values["regional_prompt"] == DOCUMENT
    assert "regional_prompt" not in bound.stripped


def test_not_treated_as_media():
    bound = _bind(_preset(_declared(["txt2img"])))
    assert bound.values["regional_prompt"]["items"][0]["caption"] == "../../etc/passwd"


def test_dropped_when_the_preset_declares_nothing():
    bound = _bind(_preset())
    assert "regional_prompt" not in bound.values
    assert "regional_prompt" in bound.stripped


def test_dropped_for_a_mode_the_preset_does_not_list():
    bound = _bind(_preset(_declared(["inpaint"]), modes=("txt2img", "inpaint")))
    assert "regional_prompt" not in bound.values
    assert "regional_prompt" in bound.stripped


def test_malformed_declaration_drops_the_value():
    bound = _bind(_preset({"regional_prompt": "yes"}))
    assert "regional_prompt" not in bound.values


def _bind_value(value):
    return bind_form(
        _preset(_declared(["txt2img"])), "txt2img", None, {"regional_prompt": value}, "user_1",
        storage_dir="/nonexistent/storage",
    )


def _item(**over):
    return {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "caption": "cat", **over}


def test_valid_document_passes_unchanged():
    document = {"enabled": True, "items": [_item()]}
    assert _bind_value(document).values["regional_prompt"] == document


@pytest.mark.parametrize("document", [
    {"enabled": True, "items": "nope"},
    {"enabled": True, "items": [_item(x="left")]},
    {"enabled": True, "items": [_item(w=0)]},
])
def test_malformed_document_is_refused_with_a_field_error(document):
    with pytest.raises(FormBindingError) as caught:
        _bind_value(document)
    assert "regional_prompt" in caught.value.field_errors


def test_undeclared_preset_does_not_validate_the_value():
    bound = bind_form(
        _preset(), "txt2img", None, {"regional_prompt": {"items": "nope"}}, "user_1",
        storage_dir="/nonexistent/storage",
    )
    assert "regional_prompt" in bound.stripped
