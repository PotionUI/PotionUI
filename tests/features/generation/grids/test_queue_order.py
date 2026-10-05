import random

import pytest

from src.features.generation.grids.expansion import expand_cells, queue_order
from tests.features.generation.grids.conftest import axis, base_request, field_index, make_user

INDEX = field_index(sampler="select", model="model", seed="seed")


def expand(x_axis, y_axis=None):
    return expand_cells(
        base_request(form_data={"sampler": "a", "model": "model:z", "seed": 1}),
        x_axis,
        y_axis,
        True,
        INDEX,
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(1),
    )


def order(cells):
    return [(c.x, c.y) for c in queue_order(cells)]


def test_without_a_model_axis_the_queue_is_plain_row_major():
    cells = expand(axis("sampler", ["a", "b", "c"]), axis("sampler2", [1, 2]))

    assert order(cells) == [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]


def test_a_model_on_the_y_axis_groups_the_queue_by_model():
    cells = expand(axis("sampler", ["a", "b"]), axis("model", ["model:1", "model:2", "model:1"], type="model"))

    assert order(cells) == [(0, 0), (1, 0), (0, 2), (1, 2), (0, 1), (1, 1)]


def test_a_model_on_the_x_axis_groups_columns_and_keeps_row_major_inside_a_group():
    cells = expand(axis("model", ["model:1", "model:2", "model:1"], type="model"), axis("sampler", ["a", "b"]))

    assert order(cells) == [(0, 0), (2, 0), (0, 1), (2, 1), (1, 0), (1, 1)]


def test_group_order_follows_the_first_appearance_in_row_major_order():
    cells = expand(axis("model", ["model:2", "model:1"], type="model"), axis("sampler", ["a"]))

    assert order(cells) == [(0, 0), (1, 0)]


def test_models_are_recognised_by_the_preset_field_type_when_the_axis_type_is_blank():
    cells = expand(axis("sampler", ["a", "b"]), axis("model", ["model:1", "model:2", "model:1"], type=""))

    assert order(cells) == [(0, 0), (1, 0), (0, 2), (1, 2), (0, 1), (1, 1)]


def test_on_screen_positions_do_not_change_with_the_queue_order():
    cells = expand(axis("sampler", ["a", "b"]), axis("model", ["model:1", "model:2", "model:1"], type="model"))

    queue_order(cells)

    assert [(c.x, c.y) for c in cells] == [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]
    assert [c.request.form_data["model"] for c in cells] == [
        "model:1", "model:1", "model:2", "model:2", "model:1", "model:1",
    ]


@pytest.mark.asyncio
async def test_the_service_submits_cells_in_queue_order(harness):
    from tests.features.generation.grids.conftest import base_request as request_factory
    from src.features.generation.grids.dto import CreateGridRequest

    harness.index = INDEX
    body = CreateGridRequest(
        request=request_factory(form_data={"sampler": "a", "model": "model:z", "seed": 1}),
        x_axis=axis("sampler", ["a", "b"]),
        y_axis=axis("model", ["model:1", "model:2", "model:1"], type="model"),
    )

    await harness.service.create(make_user("u1"), body)

    assert [(s["ref"].x, s["ref"].y) for s in harness.submitted] == [(0, 0), (1, 0), (0, 2), (1, 2), (0, 1), (1, 1)]
