from __future__ import annotations

from pathlib import Path

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader

MODELS = {"latent": "upscale_model", "pixel_spatial": "pixel_spatial_lora", "refine_details": "refine_details_lora"}
DESCRIPTIONS = {
    "upscale_model": "Used by Standard.",
    "pixel_spatial_lora": "Used by Generative 2x.",
    "refine_details_lora": "Used by Refine details.",
}


@pytest.fixture(scope="module")
def ltx25_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5")), None)
    if template is None:
        pytest.fail("marketplace/LTX-2.5 preset not present")
    return template


def _walk(fields):
    for field in fields or []:
        yield field
        yield from _walk(field.children if isinstance(field.children, list) else None)


def _upscale_fields(template):
    found = {}
    for form in template.modes["upscale"].forms:
        for field in _walk(form.fields):
            if field.name:
                found[field.name] = field
    return found


def _field_errors(template, method, filled):
    data = {"upscale_method": method}
    data.update({MODELS[name]: "some_model.safetensors" for name in filled})
    with pytest.raises(FormBindingError) as exc:
        bind_form(template, "upscale", None, data, "user_1")
    return exc.value.field_errors


def test_all_three_method_models_are_visible_for_every_method(ltx25_template):
    fields = _upscale_fields(ltx25_template)
    for name in MODELS.values():
        assert fields[name].visible is not False
        for reaction in fields[name].reactions:
            assert reaction["then"].get("set_visibility") is None


@pytest.mark.parametrize("method", sorted(MODELS))
def test_only_the_selected_methods_model_is_required(ltx25_template, method):
    errors = _field_errors(ltx25_template, method, filled=[])

    assert MODELS[method] in errors
    for other_method, name in MODELS.items():
        if other_method != method:
            assert name not in errors


def test_refine_details_with_only_its_lora_does_not_ask_for_the_others(ltx25_template):
    errors = _field_errors(ltx25_template, "refine_details", filled=["refine_details"])

    assert not set(MODELS.values()) & set(errors)


def test_the_descriptions_name_the_method_that_uses_each_model(ltx25_template):
    fields = _upscale_fields(ltx25_template)

    for name, description in DESCRIPTIONS.items():
        assert fields[name].description == description
