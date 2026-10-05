import pytest

from src.features.generation.grids.dto import CreateGridRequest
from src.features.generation.grids.service import GridError
from src.features.plans.errors import LimitExceeded
from tests.features.generation.grids.conftest import axis, base_request


def body(x_values, y_values=None, lock_seed=True, **request_overrides):
    return CreateGridRequest(
        request=base_request(**request_overrides),
        x_axis=axis("sampler", x_values),
        y_axis=axis("steps", y_values) if y_values is not None else None,
        lock_seed=lock_seed,
    )


def refusal():
    return LimitExceeded(
        [
            {
                "kind": "generations_per_day", "code": "daily_generations_exceeded", "label": "Daily",
                "format": "count", "used": 7, "limit": 12, "incoming": 6, "needed": 6, "remaining": 5,
                "percent": 58.0, "resets_at": None, "message": "6 needed, 5 left today",
            }
        ],
        "submit",
        "Ask your admin.",
    )


@pytest.mark.asyncio
async def test_a_grid_persists_the_grid_row_and_one_generation_per_cell(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a", "b", "c"], [10, 20]))

    assert len(harness.submitted) == 6
    assert len(harness.rows("generation_grids")) == 1
    cells = harness.rows("generations", "grid_id = ?", (grid["id"],))
    assert sorted((c["grid_x"], c["grid_y"]) for c in cells) == sorted((x, y) for y in range(2) for x in range(3))
    assert grid["status"] == "running"
    assert [(c["x"], c["y"]) for c in grid["cells"]] == [(x, y) for y in range(2) for x in range(3)]
    assert grid["cells"][4]["axis_values"] == {"sampler": "b", "steps": "20"}
    assert grid["cells"][0]["status"] == "queued"


@pytest.mark.asyncio
async def test_each_cell_goes_through_the_shared_submit_with_its_own_resolved_form(harness):
    await harness.service.create(harness.users["u1"], body(["a", "b"], [10]))

    forms = {(s["ref"].x, s["ref"].y): s["request"].form_data for s in harness.submitted}
    assert forms[(0, 0)]["sampler"] == "a" and forms[(1, 0)]["sampler"] == "b"
    assert all(form["quantity"] == 1 and form["steps"] == 10 for form in forms.values())
    assert {s["user"] for s in harness.submitted} == {"u1"}


@pytest.mark.asyncio
async def test_a_grid_over_the_hard_cap_is_refused_before_anything_is_persisted(harness):
    with pytest.raises(GridError) as refused:
        await harness.service.create(harness.users["u1"], body(list(range(11)), list(range(10))))

    assert refused.value.code == "grid_too_large"
    assert refused.value.status_code == 422
    assert harness.submitted == []
    assert harness.guard.calls == []
    assert harness.rows("generation_grids") == []
    assert harness.rows("generations") == []


@pytest.mark.asyncio
async def test_exactly_one_hundred_cells_is_allowed(harness):
    grid = await harness.service.create(harness.users["u1"], body(list(range(10)), list(range(10))))

    assert len(harness.submitted) == 100
    assert len(grid["cells"]) == 100


@pytest.mark.asyncio
async def test_the_same_field_on_both_axes_is_refused(harness):
    request = CreateGridRequest(
        request=base_request(), x_axis=axis("sampler", ["a"]), y_axis=axis("sampler", ["b"])
    )

    with pytest.raises(GridError) as refused:
        await harness.service.create(harness.users["u1"], request)

    assert refused.value.code == "axis_conflict"
    assert harness.rows("generation_grids") == []


@pytest.mark.asyncio
async def test_a_plans_shortfall_refuses_the_whole_grid_and_persists_nothing(harness):
    harness.guard.refusal = refusal()

    with pytest.raises(LimitExceeded) as refused:
        await harness.service.create(harness.users["u1"], body(["a", "b", "c"], [10, 20]))

    payload = refused.value.payload()
    assert (payload["needed"], payload["remaining"]) == (6, 5)
    assert harness.submitted == []
    assert harness.rows("generation_grids") == []
    assert harness.rows("generations") == []
    request, count = harness.guard.calls[0]
    assert count == 6
    assert request.point == "submit" and request.user_id == "u1" and request.preset_id == "p1"


@pytest.mark.asyncio
async def test_the_plans_check_counts_every_cell_once(harness):
    await harness.service.create(harness.users["u1"], body(["a", "b", "c"], [10, 20, 30, 40]))

    assert [count for _, count in harness.guard.calls] == [12]


@pytest.mark.asyncio
async def test_a_cell_that_fails_to_submit_rolls_the_whole_grid_back(harness):
    harness.fail_on[3] = ValueError("boom")

    with pytest.raises(ValueError):
        await harness.service.create(harness.users["u1"], body(["a", "b", "c"], [10, 20]))

    assert harness.rows("generation_grids") == []
    assert harness.rows("generations") == []
    assert sorted(harness.cancelled) == ["gen-000", "gen-001", "gen-002"]


@pytest.mark.asyncio
async def test_a_missing_preset_is_a_404_and_persists_nothing(harness):
    harness.service.preset_loader.load_preset_by_id = lambda preset_id: None

    with pytest.raises(GridError) as refused:
        await harness.service.create(harness.users["u1"], body(["a"]))

    assert refused.value.status_code == 404
    assert harness.rows("generation_grids") == []


@pytest.mark.asyncio
async def test_unlocked_seeds_are_stored_on_the_grid_and_on_each_cell(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a", "b", "c"], lock_seed=False))

    seeds = [cell["seed"] for cell in grid["cells"]]
    assert len(set(seeds)) == 3
    stored = harness.grids.get(grid["id"]).seeds
    assert [stored[f"{x},0"] for x in range(3)] == seeds


@pytest.mark.asyncio
async def test_the_base_request_is_kept_on_the_grid_without_axes_applied(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a", "b"]))

    saved = harness.grids.get(grid["id"]).base_request
    assert saved["form_data"]["sampler"] == "euler"
    assert saved["tab_id"] == "tab-1"


@pytest.mark.asyncio
async def test_grid_status_is_derived_from_its_cells(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a", "b"]))
    user = harness.users["u1"]

    harness.set_status("gen-000", "completed")
    assert (await harness.service.get(grid["id"], user))["status"] == "running"
    harness.set_status("gen-001", "completed")
    assert (await harness.service.get(grid["id"], user))["status"] == "completed"
    harness.set_status("gen-001", "failed")
    assert (await harness.service.get(grid["id"], user))["status"] == "partial"
    harness.set_status("gen-000", "cancelled")
    harness.set_status("gen-001", "cancelled")
    assert (await harness.service.get(grid["id"], user))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_completed_cells_carry_a_thumbnail_and_failed_cells_a_plain_error(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a", "b"]))
    harness.set_status("gen-000", "completed")
    harness.set_status("gen-001", "failed", error_code="out_of_memory", error_message="CUDA out of memory: raw detail")

    cells = (await harness.service.get(grid["id"], harness.users["u1"]))["cells"]

    assert cells[0]["thumbnail_url"] == "/api/media/files/file-gen-000?size=medium"
    assert cells[0]["media_type"] == "image"
    assert cells[1]["thumbnail_url"] is None
    assert cells[1]["status"] == "failed"
    assert cells[1]["error"]
    assert "CUDA" not in cells[1]["error"]


@pytest.mark.asyncio
async def test_admins_see_the_full_failure_message(harness):
    grid = await harness.service.create(harness.users["u1"], body(["a"]))
    harness.set_status("gen-000", "failed", error_code="out_of_memory", error_message="CUDA out of memory: raw detail")

    cells = (await harness.service.get(grid["id"], harness.users["admin"]))["cells"]

    assert cells[0]["error"] == "CUDA out of memory: raw detail"


def test_confirm_above_defaults_to_24_and_reads_the_stored_setting(harness):
    assert harness.service.read_settings() == {"confirm_above": 24, "hard_cap": 100}

    harness.settings_store["compare_confirm_above"] = "40"
    assert harness.service.read_settings() == {"confirm_above": 40, "hard_cap": 100}

    harness.settings_store["compare_confirm_above"] = "junk"
    assert harness.service.read_settings()["confirm_above"] == 24
