import random

from src.features.generation.grids.expansion import (
    apply_lora_value,
    cell_idempotency_key,
    expand_cells,
    grid_shape,
    positions,
)
from tests.features.generation.grids.conftest import axis, base_request, field_index

INDEX = field_index(sampler="select", steps="number", seed="seed", quantity="number", cfg="number")


def expand(x_axis, y_axis=None, lock_seed=True, request=None, index=INDEX, **kwargs):
    return expand_cells(
        request or base_request(),
        x_axis,
        y_axis,
        lock_seed,
        index,
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(3),
        **kwargs,
    )


def test_positions_are_row_major_with_x_across_and_y_down():
    x = axis("sampler", ["euler", "dpmpp", "heun"])
    y = axis("steps", [10, 20])

    assert grid_shape(x, y) == (3, 2)
    assert positions(x, y) == [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]


def test_without_a_y_axis_the_grid_is_one_row():
    x = axis("sampler", ["euler", "dpmpp"])

    assert grid_shape(x, None) == (2, 1)
    assert positions(x, None) == [(0, 0), (1, 0)]


def test_each_cell_gets_its_own_axis_values_on_a_copy_of_the_form():
    request = base_request()
    cells = expand(axis("sampler", ["euler", "dpmpp"]), axis("steps", [10, 30]), request=request)

    by_position = {(c.x, c.y): c.request.form_data for c in cells}
    assert by_position[(0, 0)]["sampler"] == "euler" and by_position[(0, 0)]["steps"] == 10
    assert by_position[(1, 0)]["sampler"] == "dpmpp" and by_position[(1, 0)]["steps"] == 10
    assert by_position[(0, 1)]["sampler"] == "euler" and by_position[(0, 1)]["steps"] == 30
    assert by_position[(1, 1)]["sampler"] == "dpmpp" and by_position[(1, 1)]["steps"] == 30
    assert request.form_data["sampler"] == "euler" and request.form_data["steps"] == 20


def test_cells_record_display_values_per_axis_field():
    x = axis("sampler", ["euler"], labels=["Euler a"])
    y = axis("steps", [10])

    cell = expand(x, y)[0]

    assert cell.axis_values == {"sampler": "Euler a", "steps": "10"}


def test_checkbox_and_resolution_and_model_values_are_set_as_given():
    index = field_index(hires="checkbox", size="resolution", model="model", seed="seed")
    request = base_request(form_data={"hires": False, "size": "512x512", "model": "model:a", "seed": 1})
    cells = expand(
        axis("hires", [True, False], type="checkbox"),
        axis("size", ["1024x1024", "768x1344"], type="resolution"),
        index=index,
        request=request,
    )

    assert [c.request.form_data["hires"] for c in cells[:2]] == [True, False]
    assert [c.request.form_data["size"] for c in cells[:2]] == ["1024x1024", "1024x1024"]
    model_cells = expand(axis("model", ["model:b", "model:c"], type="model"), index=index, request=request)
    assert [c.request.form_data["model"] for c in model_cells] == ["model:b", "model:c"]


def test_lora_axis_replaces_the_strength_of_that_one_row():
    index = field_index(loras="lora_picker", seed="seed")
    request = base_request(
        form_data={
            "loras": [{"model": "model:a", "strength": 1.0}, {"model": "model:b", "strength": 0.5}],
            "seed": 1,
        }
    )
    lora_axis = axis(
        "loras",
        [{"lora": "model:a", "strength": 0.2}, {"lora": "model:a", "strength": 0.9}],
        type="lora_picker",
    )

    cells = expand(lora_axis, index=index, request=request)

    assert cells[0].request.form_data["loras"] == [
        {"model": "model:a", "strength": 0.2},
        {"model": "model:b", "strength": 0.5},
    ]
    assert cells[1].request.form_data["loras"][0]["strength"] == 0.9
    assert request.form_data["loras"][0]["strength"] == 1.0


def test_lora_axis_adds_the_row_when_it_is_not_in_the_form():
    rows = apply_lora_value([{"model": "model:b", "strength": 0.5}], {"lora": "model:a", "strength": 0.7})

    assert rows == [{"model": "model:b", "strength": 0.5}, {"model": "model:a", "strength": 0.7}]


def test_lora_axis_accepts_a_ref_object_and_an_empty_form_field():
    rows = apply_lora_value(None, {"lora": {"model": "model:z"}, "strength": 1})

    assert rows == [{"model": "model:z", "strength": 1.0}]


def test_quantity_is_forced_to_one_and_extra_prompts_are_dropped():
    request = base_request(
        prompts=[{"positive": "one", "negative": ""}, {"positive": "two", "negative": ""}],
    )

    cells = expand(axis("sampler", ["euler", "heun"]), request=request, quantity_fields=["quantity"])

    for cell in cells:
        assert cell.request.form_data["quantity"] == 1
        assert [pair.positive for pair in cell.request.prompts] == ["one"]


def test_only_the_fields_the_pipeline_reads_as_quantity_are_forced_to_one():
    index = field_index(sampler="select", seed="seed", count="stepper", quantity="number")
    request = base_request(form_data={"sampler": "euler", "seed": 1, "count": 4, "quantity": 4})

    cells = expand(axis("sampler", ["euler", "heun"]), request=request, index=index, quantity_fields=["count"])

    assert [cell.request.form_data["count"] for cell in cells] == [1, 1]
    assert [cell.request.form_data["quantity"] for cell in cells] == [4, 4]


def test_a_declared_quantity_field_missing_from_the_form_is_not_invented():
    cells = expand(axis("sampler", ["euler"]), quantity_fields=["batch"])

    assert "batch" not in cells[0].request.form_data


def test_the_base_request_is_not_mutated_by_expansion():
    request = base_request()
    before = request.model_dump()

    expand(axis("sampler", ["a", "b"]), axis("steps", [1, 2]), request=request)

    assert request.model_dump() == before


def test_cell_idempotency_keys_are_derived_per_position():
    assert cell_idempotency_key(None, 1, 2) is None
    assert cell_idempotency_key("k", 1, 2) == "k:grid:1:2"
    assert cell_idempotency_key("k" * 400, 0, 0).endswith(":grid:0:0")
    assert len(cell_idempotency_key("k" * 400, 0, 0)) <= 200

    cells = expand(axis("sampler", ["a", "b"]), request=base_request(idempotency_key="abc"))

    assert [c.request.idempotency_key for c in cells] == ["abc:grid:0:0", "abc:grid:1:0"]


def test_only_restricts_expansion_to_the_wanted_positions():
    x = axis("sampler", ["a", "b", "c"])
    y = axis("steps", [1, 2])

    cells = expand(x, y, only=[(2, 1), (0, 0)])

    assert [(c.x, c.y) for c in cells] == [(2, 1), (0, 0)]
    assert cells[0].request.form_data["sampler"] == "c" and cells[0].request.form_data["steps"] == 2
