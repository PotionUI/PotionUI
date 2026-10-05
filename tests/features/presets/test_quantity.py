from types import SimpleNamespace

from src.features.presets.quantity import mode_quantity_fields, preset_quantity_fields
from src.features.presets.templates import ModeTemplate, PipeTemplate


def mode(*configs):
    return ModeTemplate(forms=[], pipes=[PipeTemplate(name=f"pipe_{i}", configuration=c) for i, c in enumerate(configs)])


def test_the_form_field_behind_a_pipe_quantity_is_the_quantity_field_whatever_it_is_called():
    assert mode_quantity_fields(mode({"quantity": "{{ form.count or 1 }}"})) == ["count"]
    assert mode_quantity_fields(mode({"quantity": "{{ form.quantity | default(1) }}"})) == ["quantity"]


def test_every_pipe_is_read_once_and_other_keys_and_literals_are_ignored():
    fields = mode_quantity_fields(
        mode(
            {"seed": "{{ form.seed }}", "quantity": "{{ form.count or 1 }}"},
            {"quantity": "{{ form.count or 1 }}", "steps": "{{ form.steps }}"},
            {"quantity": 2},
            {"quantity": "form.literal outside a template"},
            None,
        )
    )

    assert fields == ["count"]


def test_dict_pipes_and_missing_modes_are_tolerated():
    data = SimpleNamespace(pipes=[{"name": "seed_generator", "configuration": {"quantity": "{{ form.batch }}"}}])

    assert mode_quantity_fields(data) == ["batch"]
    assert mode_quantity_fields(None) == []
    assert preset_quantity_fields(SimpleNamespace(modes={"txt2img": data}), "txt2img") == ["batch"]
    assert preset_quantity_fields(SimpleNamespace(modes={}), "txt2img") == []
    assert preset_quantity_fields(SimpleNamespace(), "txt2img") == []
