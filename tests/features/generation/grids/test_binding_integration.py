import random

import pytest

from src.features.forms.binding import bind_form, form_field_index
from src.features.forms.exceptions import FormNotFoundException
from src.features.generation.grids.expansion import expand_cells
from src.features.presets.templates import FieldTemplate, FormTemplate, ModeTemplate, PresetTemplate
from tests.features.generation.grids.conftest import axis, base_request


def preset():
    fields = [
        FieldTemplate(
            type="select",
            name="profile",
            default="balanced",
            configuration={"options": [{"value": "balanced"}, {"value": "fast"}, {"value": "quality"}]},
        ),
        FieldTemplate(
            type="number",
            name="steps",
            default=25,
            reactions=[
                {"when": {"field": "profile", "operator": "equals", "value": "fast"}, "then": {"set_value": 8}},
                {"when": {"field": "profile", "operator": "equals", "value": "quality"}, "then": {"set_value": 40}},
            ],
        ),
        FieldTemplate(type="seed", name="seed", default=-1),
    ]
    forms = [FormTemplate(name="custom", fields=fields, default=True, order=0)]
    return PresetTemplate(
        id="p1", name="P", version="1.0.0", path="/presets/p1", modes={"txt2img": ModeTemplate(forms=forms, pipes=[])}
    )


def test_form_field_index_flattens_the_resolved_form():
    index = form_field_index(preset(), "txt2img", None)

    assert set(index) == {"profile", "steps", "seed"}
    assert index["steps"].reactions


def test_an_unknown_mode_or_form_is_a_form_not_found_error():
    with pytest.raises(FormNotFoundException):
        form_field_index(preset(), "img2img", None)
    with pytest.raises(FormNotFoundException):
        form_field_index(preset(), "txt2img", "nope")


def test_expanded_cells_keep_their_reaction_values_through_the_real_bind_form():
    template = preset()
    index = form_field_index(template, "txt2img", None)
    request = base_request(form_data={"profile": "balanced", "steps": 25, "seed": 5})

    cells = expand_cells(
        request, axis("profile", ["fast", "balanced", "quality"]), None, True, index,
        preset_id="p1", mode="txt2img", rng=random.Random(1),
    )
    bound = [bind_form(template, "txt2img", None, cell.request.form_data, "u1").values for cell in cells]

    assert [values["steps"] for values in bound] == [8, 25, 40]
    assert [values["profile"] for values in bound] == ["fast", "balanced", "quality"]
    assert {values["seed"] for values in bound} == {5}
