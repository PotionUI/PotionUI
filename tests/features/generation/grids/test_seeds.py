import random

from src.features.generation.grids.expansion import cell_key, expand_cells
from tests.features.generation.grids.conftest import axis, base_request, field_index

INDEX = field_index(sampler="select", steps="number", seed="seed", quantity="number")


def expand(x_axis, y_axis=None, lock_seed=True, request=None, **kwargs):
    return expand_cells(
        request or base_request(),
        x_axis,
        y_axis,
        lock_seed,
        INDEX,
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(5),
        **kwargs,
    )


def test_locked_seed_uses_the_form_seed_in_every_cell():
    cells = expand(axis("sampler", ["a", "b", "c"]), lock_seed=True)

    assert {c.request.form_data["seed"] for c in cells} == {111}
    assert {c.seed for c in cells} == {111}


def test_locked_with_a_randomize_seed_draws_one_shared_seed():
    request = base_request(form_data={"sampler": "a", "seed": -1})

    cells = expand(axis("sampler", ["a", "b", "c"]), lock_seed=True, request=request)

    seeds = {c.request.form_data["seed"] for c in cells}
    assert len(seeds) == 1
    assert next(iter(seeds)) >= 0


def test_unlocked_draws_a_distinct_seed_for_every_cell_and_stores_it_on_the_cell():
    cells = expand(axis("sampler", ["a", "b", "c", "d"]), axis("steps", [1, 2]), lock_seed=False)

    seeds = [c.request.form_data["seed"] for c in cells]
    assert len(set(seeds)) == len(cells)
    assert seeds == [c.seed for c in cells]
    assert all(0 <= seed <= 2 ** 32 - 1 for seed in seeds)
    assert 111 not in seeds


def test_unlocked_seeds_are_reproducible_from_the_stored_map():
    first = expand(axis("sampler", ["a", "b", "c"]), lock_seed=False)
    stored = {cell_key(c.x, c.y): c.seed for c in first}

    again = expand_cells(
        base_request(),
        axis("sampler", ["a", "b", "c"]),
        None,
        False,
        INDEX,
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(99),
        stored_seeds=stored,
    )

    assert [c.request.form_data["seed"] for c in again] == [c.request.form_data["seed"] for c in first]


def test_a_seed_axis_is_never_overridden_by_the_cell_seed():
    x = axis("seed", [10, 20, 30], type="seed")

    cells = expand(x, lock_seed=False)

    assert [c.request.form_data["seed"] for c in cells] == [10, 20, 30]
    assert [c.seed for c in cells] == [10, 20, 30]


def test_the_seed_is_a_non_form_field_when_the_preset_declares_no_seed_type():
    index = field_index(sampler="select")

    cells = expand_cells(
        base_request(form_data={"sampler": "a", "seed": 5}),
        axis("sampler", ["a", "b"]),
        None,
        True,
        index,
        preset_id="p1",
        mode="txt2img",
        rng=random.Random(1),
    )

    assert {c.request.form_data["seed"] for c in cells} == {5}
