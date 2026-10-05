import pytest

from src.features.generation.grids.dto import CreateGridRequest
from src.features.generation.grids.service import GridError
from src.features.plans.errors import LimitExceeded
from tests.features.generation.grids.conftest import axis, base_request


def body():
    return CreateGridRequest(
        request=base_request(idempotency_key="abc"),
        x_axis=axis("sampler", ["a", "b", "c"]),
        y_axis=axis("steps", [10, 20]),
        lock_seed=False,
    )


async def make_grid(harness, user="u1"):
    return await harness.service.create(harness.users[user], body())


@pytest.mark.asyncio
async def test_only_the_owner_or_an_admin_can_read_a_grid(harness):
    grid = await make_grid(harness)

    assert (await harness.service.get(grid["id"], harness.users["u1"]))["id"] == grid["id"]
    assert (await harness.service.get(grid["id"], harness.users["admin"]))["id"] == grid["id"]
    with pytest.raises(GridError) as refused:
        await harness.service.get(grid["id"], harness.users["u2"])
    assert refused.value.status_code == 404
    with pytest.raises(GridError) as missing:
        await harness.service.get("nope", harness.users["u1"])
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_retry_failed_resubmits_only_failed_and_deleted_positions_with_the_same_seeds(harness):
    grid = await make_grid(harness)
    for number in range(6):
        harness.set_status(f"gen-{number:03d}", "completed")
    harness.set_status("gen-001", "failed")
    harness.set_status("gen-004", "cancelled")
    harness.generations.delete("gen-002")
    first_batch = len(harness.submitted)
    original_seeds = {(s["ref"].x, s["ref"].y): s["request"].form_data["seed"] for s in harness.submitted}

    result = await harness.service.retry_failed(grid["id"], harness.users["u1"])

    retried = harness.submitted[first_batch:]
    assert sorted((s["ref"].x, s["ref"].y) for s in retried) == [(1, 0), (2, 0)]
    for submission in retried:
        position = (submission["ref"].x, submission["ref"].y)
        assert submission["request"].form_data["seed"] == original_seeds[position]
    assert {s["request"].idempotency_key for s in retried}.isdisjoint(
        {s["request"].idempotency_key for s in harness.submitted[:first_batch]}
    )
    assert "gen-001" not in [row["id"] for row in harness.rows("generations")]
    statuses = {(c["x"], c["y"]): c["status"] for c in result["cells"]}
    assert statuses[(1, 0)] == "queued" and statuses[(2, 0)] == "queued"
    assert statuses[(1, 1)] == "cancelled"


@pytest.mark.asyncio
async def test_retry_failed_with_nothing_to_retry_submits_nothing(harness):
    grid = await make_grid(harness)
    for number in range(6):
        harness.set_status(f"gen-{number:03d}", "completed")
    before = len(harness.submitted)

    await harness.service.retry_failed(grid["id"], harness.users["u1"])

    assert len(harness.submitted) == before


@pytest.mark.asyncio
async def test_retry_failed_is_refused_atomically_by_plans(harness):
    grid = await make_grid(harness)
    harness.set_status("gen-000", "failed")
    harness.set_status("gen-001", "failed")
    before = len(harness.submitted)
    harness.guard.refusal = LimitExceeded(
        [{"kind": "generations_per_day", "code": "daily_generations_exceeded", "label": "d", "format": "count",
          "used": 1, "limit": 1, "incoming": 2, "needed": 2, "remaining": 0, "percent": 100.0,
          "resets_at": None, "message": "m"}],
        "submit",
        "",
    )

    with pytest.raises(LimitExceeded):
        await harness.service.retry_failed(grid["id"], harness.users["u1"])

    assert len(harness.submitted) == before
    assert harness.guard.calls[-1][1] == 2


@pytest.mark.asyncio
async def test_retry_failed_is_owner_only(harness):
    grid = await make_grid(harness)

    with pytest.raises(GridError):
        await harness.service.retry_failed(grid["id"], harness.users["u2"])


@pytest.mark.asyncio
async def test_a_deleted_cell_shows_as_a_hole_with_its_axis_values(harness):
    grid = await make_grid(harness)
    harness.generations.delete("gen-003")

    cells = (await harness.service.get(grid["id"], harness.users["u1"]))["cells"]

    hole = cells[3]
    assert (hole["x"], hole["y"], hole["status"], hole["generation_id"]) == (0, 1, "deleted", None)
    assert hole["axis_values"] == {"sampler": "a", "steps": "20"}
    assert hole["seed"] is not None


@pytest.mark.asyncio
async def test_deleting_a_grid_cancels_active_cells_and_removes_cells_and_grid(harness):
    grid = await make_grid(harness)
    harness.set_status("gen-000", "completed")
    harness.set_status("gen-001", "running")

    await harness.service.delete(grid["id"], harness.users["u1"])

    assert sorted(harness.cancelled) == ["gen-001", "gen-002", "gen-003", "gen-004", "gen-005"]
    assert sorted(harness.history.deleted) == [f"gen-{n:03d}" for n in range(6)]
    assert harness.rows("generations") == []
    assert harness.rows("generation_grids") == []


@pytest.mark.asyncio
async def test_a_stranger_cannot_delete_a_grid_and_nothing_is_touched(harness):
    grid = await make_grid(harness)

    with pytest.raises(GridError) as refused:
        await harness.service.delete(grid["id"], harness.users["u2"])

    assert refused.value.status_code == 404
    assert len(harness.rows("generations")) == 6
    assert len(harness.rows("generation_grids")) == 1


@pytest.mark.asyncio
async def test_an_admin_can_delete_someone_elses_grid(harness):
    grid = await make_grid(harness)

    await harness.service.delete(grid["id"], harness.users["admin"])

    assert harness.rows("generation_grids") == []
    assert harness.rows("generations") == []
