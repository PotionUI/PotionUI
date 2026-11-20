import asyncio

import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks import parts, regions
from src.features.editor_tasks.runs import GENERATION_GONE, RESTART_MESSAGE
from src.platform.http.errors import DomainError
from tests.features.editor_tasks.parts_support import (
    FIX_FACES,
    UPSCALE,
    build_parts_manager,
    gradient,
    regions_mask,
    settle_part,
    structured,
    until,
    upscale_body,
    uploads_named,
)
from tests.features.editor_tasks.run_support import (
    USER,
    body,
    make_edit,
    make_users,
    restarted,
    until_terminal,
)

PICTURE = (500, 400)
TWO_FACES = [(60, 140, 60, 160), (240, 340, 300, 420)]


@pytest.fixture
def upscaling(mock_db, tmp_path):
    make_users()
    manager, fakes, tasks = build_parts_manager(tmp_path, {"UP-SCALE": UPSCALE})
    gradient(1024, 1024).save(fakes.files["picture"])
    return manager, fakes, make_edit().id


@pytest.fixture
def fixing(mock_db, tmp_path):
    make_users()
    manager, fakes, tasks = build_parts_manager(tmp_path, {"FIX-FACES": FIX_FACES})
    regions_mask(PICTURE, TWO_FACES).save(fakes.files["region"])
    return manager, fakes, make_edit().id


def fix_body(edit_id, **extra):
    return body(edit_id, preset="FIX-FACES", prompt=None, **extra)


async def feed_tiles(manager, fakes, run, count, colour=lambda index: (index * 20, 90, 200 - index * 20)):
    for index in range(count):
        generation_id = f"g{index + 1}"
        await until(lambda: len(fakes.requests) == index + 1)
        source = Image.open(fakes.files[fakes.requests[index].form_data["source_image"].split("/")[-1].split(".")[0]])
        size = (round(source.width * 4), round(source.height * 4))
        pixels = np.full((size[1], size[0], 3), colour(index), dtype=np.uint8)
        await settle_part(manager, fakes, generation_id, results=[pixels], size=size)
    return await until_terminal(manager, USER, run["id"])


@pytest.mark.asyncio
async def test_a_1024_picture_upscaled_4x_runs_nine_tiles_one_after_another_into_a_4096_candidate(upscaling):
    manager, fakes, edit_id = upscaling

    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))

    assert run["parts"] == {"done": 0, "total": 9} and run["size"] == {"width": 4096, "height": 4096}
    assert run["rect"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert len(fakes.requests) == 1
    first = fakes.requests[0]
    assert first.form_data["upscale_factor"] == 4.0 and first.mode == "editor:edit" and first.prompts[0].positive == ""
    assert Image.open(fakes.files["up1"]).size == (1024, 1024) and Image.open(fakes.files["up2"]).size == (384, 384)

    done = await feed_tiles(manager, fakes, run, 9)

    assert done["status"] == "done", done["error"]
    assert done["parts"] == {"done": 9, "total": 9} and done["progress"] == 1.0
    assert len(fakes.requests) == 9
    assert done["size"] == {"width": 4096, "height": 4096}
    assert [c["size"] for c in done["candidates"]] == [{"width": 4096, "height": 4096}]
    patch = Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]])
    assert patch.mode == "RGBA" and patch.size == (4096, 4096) and np.asarray(patch)[..., 3].min() == 255
    assert done["candidates"][0]["generation_file_ids"] == [f"g{n}-0" for n in range(1, 10)]
    assert {g: o["task"] for g, o in fakes.origins.items()} == {f"g{n}": "upscale" for n in range(1, 10)}
    assert {o["run_id"] for o in fakes.origins.values()} == {run["id"]}
    assert fakes.messages[-1]["run"]["status"] == "done" and fakes.messages[-1]["run"]["parts"] == {"done": 9, "total": 9}
    assert fakes.messages[-1]["generation_id"] == "g1"


@pytest.mark.asyncio
async def test_each_tile_is_submitted_only_after_the_previous_one_finished(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    assert len(fakes.requests) == 1

    await asyncio.sleep(0.05)
    assert len(fakes.requests) == 1

    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await until(lambda: len(fakes.requests) == 2)
    await asyncio.sleep(0.05)

    assert len(fakes.requests) == 2
    mid = await manager.get(USER, run["id"])
    assert mid["parts"] == {"done": 1, "total": 9} and mid["status"] in ("queued", "running")
    assert mid["progress"] == pytest.approx((1 + 0.4) / 9)


@pytest.mark.asyncio
async def test_a_finished_tile_input_is_forgotten_while_the_run_goes_on(upscaling):
    manager, fakes, edit_id = upscaling
    await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))

    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await until(lambda: len(fakes.requests) == 2)
    await asyncio.sleep(0.05)

    live = fakes.live()
    inputs = [name for name, filename in live.items() if filename == "editor-input.png"]
    assert len(inputs) == 1
    assert len(uploads_named(fakes, "editor-input.png")) == 2


@pytest.mark.asyncio
async def test_the_working_files_are_gone_once_the_upscale_is_done(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))

    done = await feed_tiles(manager, fakes, run, 9)

    assert done["status"] == "done", done["error"]
    assert sorted(fakes.live().values()) == ["editor-patch.png"]


@pytest.mark.asyncio
async def test_a_target_size_picks_the_factor_the_mode_receives(upscaling):
    manager, fakes, edit_id = upscaling

    run = await manager.start(USER, "upscale", upscale_body(edit_id, target={"width": 2048, "height": 3000}))

    assert run["size"] == {"width": 2048, "height": 2048}
    assert fakes.requests[0].form_data["upscale_factor"] == 2.0
    assert run["parts"]["total"] == 9


@pytest.mark.asyncio
@pytest.mark.parametrize("extra, code", [
    ({}, "upscale_factor_required"),
    ({"scale": 2, "target": {"width": 4000, "height": 4000}}, "upscale_factor_ambiguous"),
    ({"target": {"width": 1000, "height": 1000}}, "upscale_not_larger"),
    ({"scale": 32}, "upscale_too_large"),
    ({"scale": 1.2}, "upscale_scale_unsupported"),
    ({"scale": 5}, "upscale_scale_unsupported"),
])
async def test_an_upscale_the_server_cannot_do_is_a_422_and_starts_nothing(upscaling, extra, code):
    manager, fakes, edit_id = upscaling

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "upscale", upscale_body(edit_id, **extra))

    assert refused.value.status_code == 422 and refused.value.code == code
    assert fakes.requests == [] and fakes.uploads == []


@pytest.mark.asyncio
async def test_a_scale_on_a_task_that_takes_none_is_a_422(fixing):
    manager, fakes, edit_id = fixing

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "fix_faces", fix_body(edit_id, scale=2))

    assert refused.value.code == "scale_not_supported" and fakes.requests == []


@pytest.mark.asyncio
async def test_the_upscale_offer_names_its_scale_range_and_the_task_its_plan(upscaling):
    manager, fakes, edit_id = upscaling

    entry = next(item for item in manager.tasks.offers(USER) if item["id"] == "upscale")

    assert entry["plan"] == "tiles" and entry["region"] == "none" and entry["prompt"] == "none" and entry["resizes"]
    assert entry["offers"][0]["scale"] == {"field": "upscale_factor", "min": 1.5, "max": 4.0}
    assert next(item for item in manager.tasks.offers(USER) if item["id"] == "edit_region")["plan"] == "single"


@pytest.mark.asyncio
async def test_cancelling_mid_tiles_stops_the_run_and_the_running_tile(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await until(lambda: len(fakes.requests) == 2)
    await asyncio.sleep(0.05)

    cancelled = await manager.cancel(USER, run["id"])

    assert cancelled["status"] == "cancelled" and fakes.cancelled == ["g2"]
    await settle_part(manager, fakes, "g2", status="cancelled")
    await asyncio.sleep(0.05)
    assert len(fakes.requests) == 2
    assert fakes.live() == {}
    assert (await manager.get(USER, run["id"]))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_a_cancel_between_two_tiles_does_not_leave_the_next_tile_running(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    fakes.submit_gate = asyncio.Event()
    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await asyncio.sleep(0.1)

    cancelled = await manager.cancel(USER, run["id"])
    fakes.submit_gate.set()
    await until(lambda: not manager._finishing)

    assert cancelled["status"] == "cancelled"
    final = await manager.get(USER, run["id"])
    assert final["status"] == "cancelled"
    assert fakes.cancelled == ["g1", "g2"] and len(fakes.requests) == 2
    assert manager._live == {} and fakes.live() == {}


@pytest.mark.asyncio
async def test_a_failing_tile_fails_the_run_and_the_rest_never_start(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await until(lambda: len(fakes.requests) == 2)

    await settle_part(manager, fakes, "g2", status="failed")
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "failed" and done["error"] == "The model failed" and done["parts"]["done"] == 1
    assert len(fakes.requests) == 2 and fakes.live() == {}


@pytest.mark.asyncio
async def test_a_tile_without_a_picture_fails_the_run(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    for index in range(9):
        await until(lambda: len(fakes.requests) == index + 1)
        results = [] if index == 8 else [structured(8, 8, index)]
        await settle_part(manager, fakes, f"g{index + 1}", results=results)

    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "failed" and done["error"] == "The model returned no picture"


@pytest.mark.asyncio
async def test_a_restart_in_the_middle_fails_the_run_instead_of_hanging(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))
    await settle_part(manager, fakes, "g1", results=[structured(2048, 2048, 1)], size=(2048, 2048))
    await until(lambda: len(fakes.requests) == 2)
    await asyncio.sleep(0.05)

    fresh = restarted(manager)
    fresh._jobs = {}
    fresh._cancelled = set()
    fresh._rerun = {}
    after = await fresh.get(USER, run["id"])

    assert after["status"] == "failed" and after["error"] == RESTART_MESSAGE


@pytest.mark.asyncio
async def test_a_generation_that_disappeared_fails_the_run(upscaling):
    manager, fakes, edit_id = upscaling
    run = await manager.start(USER, "upscale", upscale_body(edit_id, scale=4))

    fakes.gone = True
    after = await manager.get(USER, run["id"])

    assert after["status"] == "failed" and after["error"] == GENERATION_GONE


@pytest.mark.asyncio
async def test_two_marked_areas_become_two_parts_and_two_candidates_compose_the_kth_result_of_each(fixing):
    manager, fakes, edit_id = fixing

    run = await manager.start(USER, "fix_faces", fix_body(edit_id, candidates=2))

    assert run["parts"] == {"done": 0, "total": 2} and run["size"] is None
    assert len(fakes.requests) == 1 and fakes.requests[0].form_data["quantity"] == 2
    picture = np.asarray(Image.open(fakes.files["picture"]).convert("RGB"))
    found = parts.Components(np.asarray(Image.open(fakes.files["region"]).convert("L")))
    wanted = parts.union_box([
        regions.plan(picture.shape, found.mask(index), "crop", 0.5, 1048576, 16, None).box for index in range(2)
    ])
    assert run["rect"] == {
        "x": wanted[2] / 500, "y": wanted[0] / 400, "width": (wanted[3] - wanted[2]) / 500, "height": (wanted[1] - wanted[0]) / 400
    }

    sizes = []
    for index in range(2):
        await until(lambda: len(fakes.requests) == index + 1)
        source = Image.open(fakes.files[fakes.requests[index].form_data["source_image"].split("/")[-1].split(".")[0]])
        sizes.append(source.size)
        results = [structured(source.width, source.height, 10 * index + k) for k in range(2)]
        await settle_part(manager, fakes, f"g{index + 1}", results=results, size=source.size)
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "done" and len(done["candidates"]) == 2
    assert [c["generation_file_ids"] for c in done["candidates"]] == [["g1-0", "g2-0"], ["g1-1", "g2-1"]]
    picture = np.asarray(Image.open(fakes.files["picture"]).convert("RGB"))
    found = parts.Components(np.asarray(Image.open(fakes.files["region"]).convert("L")))
    union = wanted
    for k, candidate in enumerate(done["candidates"]):
        patch = np.asarray(Image.open(fakes.files[candidate["patch"]["item_id"]]))
        assert patch.shape == (union[1] - union[0], union[3] - union[2], 4)
        placed = []
        for index in range(2):
            geometry = regions.plan(
                picture.shape, found.mask(index), "crop", 0.5, 1048576, 16, None
            )
            top, bottom, left, right = geometry.box
            result = Image.fromarray(structured(*sizes[index], 10 * index + k))
            placed.append(
                (geometry.box, regions.stitch(picture[top:bottom, left:right], found.mask(index)[top:bottom, left:right], result, geometry))
            )
        assert np.array_equal(patch, parts.compose_patches(placed, union))
    first, second = (np.asarray(Image.open(fakes.files[c["patch"]["item_id"]])) for c in done["candidates"])
    assert not np.array_equal(first, second)
    assert first[..., 3].max() == 255 and first[0, 0, 3] == 0


@pytest.mark.asyncio
async def test_the_candidate_count_is_the_smallest_any_part_returned(fixing):
    manager, fakes, edit_id = fixing
    run = await manager.start(USER, "fix_faces", fix_body(edit_id, candidates=2))

    for index in range(2):
        await until(lambda: len(fakes.requests) == index + 1)
        source = Image.open(fakes.files[fakes.requests[index].form_data["source_image"].split("/")[-1].split(".")[0]])
        results = [structured(source.width, source.height, k) for k in range(2 if index == 0 else 1)]
        await settle_part(manager, fakes, f"g{index + 1}", results=results, size=source.size)
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "done" and len(done["candidates"]) == 1


@pytest.mark.asyncio
async def test_progress_combines_the_finished_parts_with_the_running_one(fixing):
    manager, fakes, edit_id = fixing
    run = await manager.start(USER, "fix_faces", fix_body(edit_id))
    queued = await manager.get(USER, run["id"])
    assert queued["progress"] == pytest.approx(0.4 / 2)

    source = Image.open(fakes.files[fakes.requests[0].form_data["source_image"].split("/")[-1].split(".")[0]])
    await settle_part(manager, fakes, "g1", results=[structured(source.width, source.height, 1)], size=source.size)
    await until(lambda: len(fakes.requests) == 2)
    await asyncio.sleep(0.05)

    mid = await manager.get(USER, run["id"])
    assert mid["parts"] == {"done": 1, "total": 2} and mid["progress"] == pytest.approx((1 + 0.4) / 2)


@pytest.mark.asyncio
async def test_a_single_marked_area_is_a_run_of_one_part(fixing):
    manager, fakes, edit_id = fixing
    regions_mask(PICTURE, TWO_FACES[:1]).save(fakes.files["region"])

    run = await manager.start(USER, "fix_faces", fix_body(edit_id))
    source = Image.open(fakes.files[fakes.requests[0].form_data["source_image"].split("/")[-1].split(".")[0]])
    await settle_part(manager, fakes, "g1", results=[structured(source.width, source.height, 3)], size=source.size)
    done = await until_terminal(manager, USER, run["id"])

    assert run["parts"] == {"done": 0, "total": 1} and done["status"] == "done" and done["parts"] == {"done": 1, "total": 1}
    assert len(done["candidates"]) == 1


@pytest.mark.asyncio
async def test_the_strength_overrides_its_form_field_and_is_clamped_to_the_range(fixing):
    manager, fakes, edit_id = fixing
    await manager.start(USER, "fix_faces", fix_body(edit_id, strength=0.9))
    assert fakes.requests[-1].form_data["denoise"] == 0.6

    await manager.start(USER, "fix_faces", fix_body(edit_id, strength=0.0))
    assert fakes.requests[-1].form_data["denoise"] == 0.1

    await manager.start(USER, "fix_faces", fix_body(edit_id, strength=0.25))
    assert fakes.requests[-1].form_data["denoise"] == 0.25

    await manager.start(USER, "fix_faces", fix_body(edit_id))
    assert fakes.requests[-1].form_data["denoise"] == 0.35


@pytest.mark.asyncio
async def test_too_many_separate_areas_are_a_422(fixing):
    manager, fakes, edit_id = fixing
    boxes = [(10 + 20 * (n // 8), 20 + 20 * (n // 8), 10 + 30 * (n % 8), 30 + 30 * (n % 8)) for n in range(parts.MAX_PARTS + 1)]
    regions_mask(PICTURE, boxes).save(fakes.files["region"])

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "fix_faces", fix_body(edit_id))

    assert refused.value.code == "too_many_regions" and refused.value.status_code == 422 and fakes.requests == []


@pytest.mark.asyncio
async def test_fix_faces_needs_a_marked_area(fixing):
    manager, fakes, edit_id = fixing
    Image.fromarray(np.zeros((400, 500), dtype=np.uint8)).save(fakes.files["region"])

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "fix_faces", fix_body(edit_id))

    assert refused.value.code == "region_empty" and fakes.requests == []
    with pytest.raises(DomainError) as missing:
        await manager.start(USER, "fix_faces", fix_body(edit_id, region=False))
    assert missing.value.code == "region_required"


@pytest.mark.asyncio
async def test_a_first_part_that_cannot_be_submitted_leaves_nothing_behind(fixing):
    manager, fakes, edit_id = fixing
    fakes.submit_error = RuntimeError("busy")

    with pytest.raises(RuntimeError):
        await manager.start(USER, "fix_faces", fix_body(edit_id))

    assert fakes.live() == {}
    assert manager.repository.find("none") is None


@pytest.mark.asyncio
async def test_the_single_part_task_keeps_its_stored_shape_and_gains_parts_and_size(mock_db, tmp_path):
    make_users()
    manager, fakes, tasks = build_parts_manager(tmp_path, {"A-CROP": {
        "schema_version": 1, "task": "edit_region", "from": "edit", "region": "crop", "inputs": {"source": "source_image"},
    }})
    run = await manager.start(USER, "edit_region", body(make_edit().id, preset="A-CROP"))
    fakes.add_result("f1")
    fakes.generation.status = "completed"
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": "completed"})
    done = await until_terminal(manager, USER, run["id"])
    stored = manager.repository.find(run["id"]).crop

    assert run["parts"] == {"done": 0, "total": 1} and done["parts"] == {"done": 1, "total": 1} and done["size"] is None
    assert "parts" not in stored and "kind" not in stored and stored["strategy"] == "crop"


@pytest.fixture
def quantityless(mock_db, tmp_path):
    make_users()
    manager, fakes, tasks = build_parts_manager(tmp_path, {"FIX-FACES": FIX_FACES}, without=("quantity",))
    regions_mask(PICTURE, TWO_FACES).save(fakes.files["region"])
    return manager, fakes, make_edit().id


@pytest.mark.asyncio
async def test_a_mode_without_a_quantity_field_runs_each_part_once_per_candidate(quantityless):
    manager, fakes, edit_id = quantityless

    run = await manager.start(USER, "fix_faces", fix_body(edit_id, candidates=2, seed=10))

    assert run["parts"] == {"done": 0, "total": 4}
    picture = np.asarray(Image.open(fakes.files["picture"]).convert("RGB"))
    found = parts.Components(np.asarray(Image.open(fakes.files["region"]).convert("L")))
    geometries = [regions.plan(picture.shape, found.mask(i), "crop", 0.5, 1048576, 16, None) for i in range(2)]
    seeds = []
    for index in range(4):
        await until(lambda: len(fakes.requests) == index + 1)
        form = fakes.requests[index].form_data
        source = Image.open(fakes.files[form["source_image"].split("/")[-1].split(".")[0]])
        seeds.append(form["seed"])
        await settle_part(manager, fakes, f"g{index + 1}", results=[structured(*source.size, index)], size=source.size)
    done = await until_terminal(manager, USER, run["id"])

    assert seeds == [10, 10, 11, 11]
    assert "quantity" not in fakes.requests[0].form_data
    assert done["status"] == "done" and len(done["candidates"]) == 2
    assert [c["generation_file_ids"] for c in done["candidates"]] == [["g1-0", "g2-0"], ["g3-0", "g4-0"]]
    union = parts.union_box([g.box for g in geometries])
    for k, candidate in enumerate(done["candidates"]):
        patch = np.asarray(Image.open(fakes.files[candidate["patch"]["item_id"]]))
        placed = []
        for i, geometry in enumerate(geometries):
            top, bottom, left, right = geometry.box
            size = Image.open(fakes.files[f"g{2 * k + i + 1}-0"]).size
            result = Image.fromarray(structured(*size, 2 * k + i))
            cut = (picture[top:bottom, left:right], found.mask(i)[top:bottom, left:right])
            placed.append((geometry.box, regions.stitch(cut[0], cut[1], result, geometry)))
        assert np.array_equal(patch, parts.compose_patches(placed, union))
