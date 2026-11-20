import numpy as np
import pytest
from PIL import Image

from src.features.editor_tasks.records import EditorRun
from src.features.editor_tasks.registry import REMOVE_OBJECT_PROMPT
from src.features.editor_tasks.runs import MESSAGE_TYPE, RESTART_MESSAGE
from src.platform.http.errors import DomainError, NotFoundError
from tests.features.editor_tasks.support import task
from tests.features.editor_tasks.run_support import (
    OTHER,
    USER,
    body,
    build_manager,
    make_edit,
    make_users,
    until_terminal,
)


@pytest.fixture
def setup(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path)
    return manager, fakes, make_edit().id


async def finish(manager, fakes, run, status="completed", count=1):
    fakes.generation.status = status
    for index in range(count):
        fakes.add_result(f"f{index + 1}")
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": status})
    return await until_terminal(manager, USER, run["id"])


@pytest.mark.asyncio
async def test_a_run_goes_from_request_to_stitched_patches(setup):
    manager, fakes, edit_id = setup

    run = await manager.start(USER, "edit_region", body(edit_id, candidates=2))
    done = await finish(manager, fakes, run, count=2)

    request = fakes.requests[0]
    assert request.preset_id == "A-CROP" and request.mode == "editor:edit" and request.prompts[0].positive == "a red wool scarf"
    assert request.form_data["source_image"] == "uploads/up1.png"
    assert request.form_data["quantity"] == 2 and request.form_data["steps"] == 20
    assert fakes.origins["g1"] == {"kind": "editor", "edit_id": edit_id, "step_id": "s7", "task": "edit_region", "run_id": run["id"]}
    assert done["status"] == "done" and done["rect"]["width"] > 0
    assert [c["generation_file_id"] for c in done["candidates"]] == ["f1", "f2"]
    assert [c["seed"] for c in done["candidates"]] == [100, 101]
    patch = Image.open(fakes.files[done["candidates"][0]["patch"]["item_id"]])
    assert patch.mode == "RGBA"
    assert {item["filename"]: item["purpose"] for item in fakes.uploads} == {
        "editor-input.png": "editor_run_working", "editor-base.png": "editor_run_working",
        "editor-region.png": "editor_run_working", "editor-patch.png": "derived_artifact",
    }
    assert [m["type"] for m in fakes.messages] == [MESSAGE_TYPE, MESSAGE_TYPE]
    assert fakes.messages[-1]["run"]["status"] == "done" and fakes.messages[-1]["generation_id"] == "g1"


@pytest.mark.asyncio
async def test_owned_fields_overwrite_what_the_caller_sent(setup):
    manager, fakes, edit_id = setup
    data = body(edit_id, seed=77)
    data.setup.form_data.update({"source_image": "uploads/evil.png", "quantity": 9, "seed": 1})

    await manager.start(USER, "edit_region", data)

    form = fakes.requests[0].form_data
    assert form["source_image"] == "uploads/up1.png" and form["quantity"] == 4 and form["seed"] == 77


@pytest.mark.asyncio
async def test_the_count_in_the_form_sets_the_quantity_and_the_old_request_field_fills_in_when_it_is_missing(setup):
    manager, fakes, edit_id = setup
    data = body(edit_id, candidates=2)
    data.setup.form_data["quantity"] = 3
    await manager.start(USER, "edit_region", data)
    assert fakes.requests[-1].form_data["quantity"] == 3

    legacy = body(edit_id, candidates=2)
    legacy.setup.form_data.pop("quantity", None)
    await manager.start(USER, "edit_region", legacy)
    assert fakes.requests[-1].form_data["quantity"] == 2

    zero = body(edit_id, candidates=2)
    zero.setup.form_data["quantity"] = 0
    await manager.start(USER, "edit_region", zero)
    assert fakes.requests[-1].form_data["quantity"] == 1


@pytest.mark.asyncio
async def test_the_mask_strategy_sends_the_whole_picture_and_a_mask(setup):
    manager, fakes, edit_id = setup

    await manager.start(USER, "edit_region", body(edit_id, preset="B-MASK"))

    form = fakes.requests[0].form_data
    assert form["source_image"] == "uploads/up1.png" and form["source_image_inpaint_mask"] == "uploads/up2.png"
    assert Image.open(fakes.files["up1"]).size == (500, 400)


@pytest.mark.asyncio
async def test_the_run_submits_the_editor_key_as_an_editor_run(setup):
    manager, fakes, edit_id = setup

    await manager.start(USER, "edit_region", body(edit_id, mode="editor:edit"))

    request = fakes.requests[0]
    assert request.mode == "editor:edit" and request._editor_run is True and request.form_name is None


@pytest.mark.asyncio
async def test_a_body_with_a_generation_mode_name_is_a_plain_422_and_starts_nothing(setup):
    manager, fakes, edit_id = setup

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "edit_region", body(edit_id, mode="edit"))

    assert refused.value.status_code == 422 and refused.value.code == "task_not_offered"
    assert fakes.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["nope", "edit", "editor:nope"])
async def test_a_mode_that_matches_no_editor_mode_is_a_plain_422(setup, mode):
    manager, fakes, edit_id = setup

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "edit_region", body(edit_id, mode=mode))

    assert refused.value.code == "task_not_offered" and refused.value.status_code == 422 and fakes.requests == []


@pytest.mark.asyncio
async def test_fixed_values_overwrite_what_the_caller_sent(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path, presets={"A-CROP": task(fixed={"match_source": True})})
    data = body(make_edit().id)
    data.setup.form_data["match_source"] = False

    await manager.start(USER, "edit_region", data)

    assert fakes.requests[0].form_data["match_source"] is True


@pytest.mark.asyncio
async def test_a_crop_with_a_mask_input_hands_over_a_crop_sized_mask(mock_db, tmp_path):
    make_users()
    spec = task(region="crop", inputs={"source": "source_image", "mask": "source_image_inpaint_mask"})
    manager, fakes = build_manager(tmp_path, presets={"A-CROP": spec})

    run = await manager.start(USER, "edit_region", body(make_edit().id))

    form = fakes.requests[0].form_data
    assert form["source_image"] == "uploads/up1.png" and form["source_image_inpaint_mask"] == "uploads/up2.png"
    picture, mask = Image.open(fakes.files["up1"]), Image.open(fakes.files["up2"])
    assert mask.size == picture.size and picture.size != (500, 400)
    assert set(np.unique(np.asarray(mask.convert("L")))) <= {0, 255} and np.asarray(mask.convert("L")).any()
    assert run["rect"]["width"] < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("index, expected", [(0, ["uploads/up1.png", "old.png"]), (1, ["old.png", "uploads/up1.png"])])
async def test_a_media_list_input_inserts_the_picture_at_its_index(mock_db, tmp_path, index, expected):
    make_users()
    spec = task(inputs={"source": {"field": "references", "index": index}})
    manager, fakes = build_manager(tmp_path, presets={"A-CROP": spec})
    data = body(make_edit().id)
    data.setup.form_data["references"] = ["old.png"]

    await manager.start(USER, "edit_region", data)

    assert fakes.requests[0].form_data["references"] == expected


@pytest.mark.asyncio
async def test_a_hidden_field_is_pinned_by_binding_not_dropped_from_the_form(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path, presets={"A-CROP": task(fields={"hide": ["resolution"]})})

    await manager.start(USER, "edit_region", body(make_edit().id))

    assert fakes.requests[0].mode == "editor:edit"
    assert "resolution" not in fakes.requests[0].form_data


@pytest.mark.asyncio
async def test_remove_object_fills_the_built_in_prompt(setup):
    manager, fakes, edit_id = setup
    data = body(edit_id, prompt="ignored")

    await manager.start(USER, "remove_object", data)

    assert fakes.requests[0].prompts[0].positive == REMOVE_OBJECT_PROMPT


@pytest.mark.asyncio
async def test_a_task_that_needs_a_prompt_refuses_without_one(setup):
    manager, fakes, edit_id = setup

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "edit_region", body(edit_id, prompt="  "))

    assert refused.value.code == "prompt_required" and fakes.requests == []


@pytest.mark.asyncio
async def test_a_task_that_needs_an_area_refuses_without_one(setup):
    manager, fakes, edit_id = setup

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remove_object", body(edit_id, region=False))

    assert refused.value.code == "region_required"


@pytest.mark.asyncio
async def test_edit_region_without_a_region_edits_the_whole_picture(setup):
    manager, fakes, edit_id = setup

    run = await manager.start(USER, "edit_region", body(edit_id, region=False))

    assert run["rect"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


@pytest.mark.asyncio
async def test_another_users_edit_is_not_found(setup):
    manager, fakes, edit_id = setup

    with pytest.raises(NotFoundError) as refused:
        await manager.start(OTHER, "edit_region", body(edit_id))

    assert refused.value.code == "edit_not_found" and fakes.requests == [] and fakes.uploads == []


@pytest.mark.asyncio
async def test_a_deleted_edit_is_not_found(setup):
    manager, fakes, edit_id = setup
    manager.edits.delete("u1", edit_id)

    with pytest.raises(NotFoundError):
        await manager.start(USER, "edit_region", body(edit_id))


@pytest.mark.asyncio
async def test_a_preset_the_caller_may_not_use_is_refused(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path, visible=lambda user: {"B-MASK"})

    with pytest.raises(NotFoundError) as refused:
        await manager.start(USER, "edit_region", body(make_edit().id, preset="A-CROP"))

    assert refused.value.code == "preset_not_found"


@pytest.mark.asyncio
async def test_a_mode_that_does_not_offer_the_task_is_refused(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path, presets={"A-CROP": None})

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "edit_region", body(make_edit().id))

    assert refused.value.code == "task_not_offered"


@pytest.mark.asyncio
async def test_an_unknown_task_is_not_found(setup):
    manager, fakes, edit_id = setup

    with pytest.raises(NotFoundError):
        await manager.start(USER, "paint_walls", body(edit_id))


@pytest.mark.asyncio
async def test_another_users_run_is_not_found_for_get_and_cancel(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))

    for call in (manager.get, manager.cancel):
        with pytest.raises(NotFoundError):
            await call(OTHER, run["id"])
    assert fakes.cancelled == []


@pytest.mark.asyncio
async def test_cancel_cancels_the_generation_and_the_run(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))

    cancelled = await manager.cancel(USER, run["id"])
    again = await manager.cancel(USER, run["id"])

    assert cancelled["status"] == "cancelled" and again["status"] == "cancelled"
    assert fakes.cancelled == ["g1"]
    assert fakes.messages[-1]["run"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_a_failed_generation_is_recorded_with_its_safe_message(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.generation.error_user_message = "Out of memory"

    done = await finish(manager, fakes, run, status="failed", count=0)

    assert done["status"] == "failed" and done["error"] == "Out of memory" and done["candidates"] == []


@pytest.mark.asyncio
async def test_a_generation_with_no_picture_fails_the_run(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))

    done = await finish(manager, fakes, run, count=0)

    assert done["status"] == "failed" and "no picture" in done["error"]


@pytest.mark.asyncio
async def test_a_refused_submit_leaves_no_run_behind(setup):
    manager, fakes, edit_id = setup
    fakes.submit_error = DomainError("limit_exceeded", "Over your limit", 403)

    with pytest.raises(DomainError):
        await manager.start(USER, "edit_region", body(edit_id))

    assert manager.repository.by_generation("g1") is None
    from src.platform.database import get_cursor
    with get_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM editor_runs")
        assert cursor.fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_generation_that_finished_during_submit_is_still_picked_up(setup):
    manager, fakes, edit_id = setup
    fakes.generation.status = "completed"
    fakes.add_result("f1")

    run = await manager.start(USER, "edit_region", body(edit_id))
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "done"


@pytest.mark.asyncio
async def test_a_run_the_server_lost_answers_failed(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    manager._live.clear()

    lost = await manager.get(USER, run["id"])

    assert lost["status"] == "failed" and lost["error"] == RESTART_MESSAGE


@pytest.mark.asyncio
async def test_a_running_generation_shows_progress(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))

    shown = await manager.get(USER, run["id"])

    assert shown["status"] == "running" and shown["progress"] == 0.4


@pytest.mark.asyncio
async def test_the_candidate_count_is_one_to_four(setup):
    manager, fakes, edit_id = setup
    with pytest.raises(ValueError):
        body(edit_id, candidates=5)
    with pytest.raises(ValueError):
        body(edit_id, candidates=0)
    for count in (1, 4):
        await manager.start(USER, "edit_region", body(edit_id, candidates=count))
        assert fakes.requests[-1].form_data["quantity"] == count


@pytest.mark.asyncio
async def test_the_surroundings_field_changes_the_crop(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path)
    edit_id = make_edit().id
    tight = body(edit_id)
    tight.setup.form_data["region_context"] = "0.25"
    wide = body(edit_id)
    wide.setup.form_data["region_context"] = "1.0"

    one = await manager.start(USER, "edit_region", tight)
    two = await manager.start(USER, "edit_region", wide)

    assert one["rect"]["width"] < two["rect"]["width"]
