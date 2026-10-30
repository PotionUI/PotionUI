from __future__ import annotations

from pathlib import Path

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader

LORAS = {"restore": "restore_lora", "deblur": "deblur_lora", "decompress": "decompress_lora"}


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


def _restore_fields(template):
    found = {}
    for form in template.modes["restore"].forms:
        for field in _walk(form.fields):
            if field.name:
                found[field.name] = field
    return found


def _field_errors(template, task, filled):
    data = {"restore_task": task}
    data.update({LORAS[name]: "some_lora.safetensors" for name in filled})
    with pytest.raises(FormBindingError) as exc:
        bind_form(template, "restore", None, data, "user_1")
    return exc.value.field_errors


def test_all_three_task_loras_are_visible_in_every_task(ltx25_template):
    fields = _restore_fields(ltx25_template)
    for name in LORAS.values():
        assert fields[name].visible is not False
        for reaction in fields[name].reactions:
            assert reaction["then"].get("set_visibility") is None


@pytest.mark.parametrize("task", sorted(LORAS))
def test_only_the_selected_tasks_lora_is_required(ltx25_template, task):
    errors = _field_errors(ltx25_template, task, filled=[])

    assert LORAS[task] in errors
    for other_task, name in LORAS.items():
        if other_task != task:
            assert name not in errors


def test_deblur_with_only_the_deblur_lora_does_not_ask_for_the_others(ltx25_template):
    errors = _field_errors(ltx25_template, "deblur", filled=["deblur"])

    assert not set(LORAS.values()) & set(errors)


def test_the_descriptions_name_the_fix_that_uses_each_lora(ltx25_template):
    fields = _restore_fields(ltx25_template)

    assert fields["restore_lora"].description == "Used by Restore old footage."
    assert fields["deblur_lora"].description == "Used by Remove blur."
    assert fields["decompress_lora"].description == "Used by Remove compression."
