import random

from src.features.generation.grids.expansion import expand_cells
from src.features.presets.templates import FieldTemplate
from tests.features.generation.grids.conftest import axis, base_request


def when(field, value, operator="equals"):
    return {"field": field, "operator": operator, "value": value}


def index():
    return {
        "profile": FieldTemplate(type="select", name="profile"),
        "seed": FieldTemplate(type="seed", name="seed"),
        "cfg": FieldTemplate(type="number", name="cfg"),
        "steps": FieldTemplate(
            type="number",
            name="steps",
            reactions=[
                {"when": when("profile", "fast"), "then": {"set_value": 8}},
                {"when": when("profile", "quality"), "then": {"set_value": 40}},
            ],
        ),
        "sharpness": FieldTemplate(
            type="number",
            name="sharpness",
            reactions=[{"when": when("steps", 8), "then": {"set_value": 0}}],
        ),
        "unrelated": FieldTemplate(
            type="number",
            name="unrelated",
            reactions=[{"when": when("cfg", 5), "then": {"set_value": -1}}],
        ),
    }


def request():
    return base_request(
        form_data={"profile": "balanced", "steps": 25, "cfg": 5, "sharpness": 2, "unrelated": 3, "seed": 1}
    )


def expand(x_axis, y_axis=None, form_index=None, req=None):
    return expand_cells(
        req or request(),
        x_axis,
        y_axis,
        True,
        form_index or index(),
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(1),
    )


def test_an_axis_on_a_trigger_field_re_runs_the_reaction_per_cell():
    cells = expand(axis("profile", ["fast", "quality"]))

    assert [c.request.form_data["steps"] for c in cells] == [8, 40]


def test_a_value_the_reaction_does_not_cover_leaves_the_form_value_alone():
    cells = expand(axis("profile", ["fast", "balanced"]))

    assert [c.request.form_data["steps"] for c in cells] == [8, 25]


def test_reactions_chain_through_fields_a_reaction_set():
    cells = expand(axis("profile", ["fast", "quality"]))

    assert [c.request.form_data["sharpness"] for c in cells] == [0, 2]


def test_reactions_that_do_not_depend_on_an_axis_field_do_not_fire():
    cells = expand(axis("profile", ["fast"]))

    assert cells[0].request.form_data["unrelated"] == 3


def test_a_field_on_the_other_axis_wins_over_the_reaction():
    cells = expand(axis("profile", ["fast", "quality"]), axis("steps", [12]))

    assert [c.request.form_data["steps"] for c in cells] == [12, 12]


def test_each_cell_is_evaluated_from_the_base_form_not_from_the_previous_cell():
    cells = expand(axis("profile", ["fast", "balanced", "quality", "balanced"]))

    assert [c.request.form_data["steps"] for c in cells] == [8, 25, 40, 25]


def test_logical_conditions_are_evaluated_with_the_same_engine_as_binding():
    form_index = index()
    form_index["steps"].reactions = [
        {
            "when": {"logic": "OR", "conditions": [when("profile", "fast"), when("profile", "turbo")]},
            "then": {"set_value": 4},
        }
    ]

    cells = expand(axis("profile", ["turbo", "balanced"]), form_index=form_index)

    assert [c.request.form_data["steps"] for c in cells] == [4, 25]


def test_an_axis_on_a_non_trigger_field_leaves_reaction_targets_untouched():
    cells = expand(axis("cfg", [3, 7]))

    assert [c.request.form_data["steps"] for c in cells] == [25, 25]
    assert [c.request.form_data["cfg"] for c in cells] == [3, 7]
