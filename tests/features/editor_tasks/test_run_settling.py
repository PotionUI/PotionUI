import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.features.editor_tasks.runs import GENERATION_GONE, MESSAGE_TYPE, NOT_PLACED, RESTART_MESSAGE
from src.platform.http.errors import DomainError
from src.platform.websocket.connection_hub import ConnectionHub
from tests.features.editor_tasks.run_support import (
    USER,
    body,
    build_manager,
    make_edit,
    make_users,
    restarted,
    until_terminal,
)


@pytest.fixture
def setup(mock_db, tmp_path):
    make_users()
    manager, fakes = build_manager(tmp_path)
    return manager, fakes, make_edit().id


def patches(fakes):
    return [item for item in fakes.uploads if item["filename"] == "editor-patch.png"]


def complete(fakes, count=1, status="completed"):
    fakes.generation.status = status
    for index in range(count):
        fakes.add_result(f"f{index + 1}")


@pytest.mark.asyncio
async def test_the_hook_firing_twice_settles_once_with_one_set_of_patches(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)

    for _ in range(2):
        manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    done = await until_terminal(manager, USER, run["id"])
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    await asyncio.sleep(0.05)

    assert done["status"] == "done" and len(done["candidates"]) == 1 and len(patches(fakes)) == 1
    assert [m["run"]["status"] for m in fakes.messages if m["run"]["status"] == "done"] == ["done"]


@pytest.mark.asyncio
async def test_the_hook_racing_the_catch_up_check_settles_once(setup):
    manager, fakes, edit_id = setup
    complete(fakes)
    run = await manager.start(USER, "edit_region", body(edit_id))

    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    await asyncio.gather(manager.get(USER, run["id"]), manager.get(USER, run["id"]), manager._finish(run["id"]))
    done = await until_terminal(manager, USER, run["id"])
    await asyncio.sleep(0.05)

    assert done["status"] == "done" and len(patches(fakes)) == 1


@pytest.mark.asyncio
async def test_a_cancel_before_completion_wins_and_no_patches_are_made(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    await manager.cancel(USER, run["id"])
    complete(fakes, status="cancelled")

    manager.on_generation_complete({"generation_id": "g1", "status": "cancelled"})
    await asyncio.sleep(0.05)

    assert (await manager.get(USER, run["id"]))["status"] == "cancelled" and patches(fakes) == []


@pytest.mark.asyncio
async def test_a_cancel_after_completion_returns_the_finished_run(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    await until_terminal(manager, USER, run["id"])

    answer = await manager.cancel(USER, run["id"])

    assert answer["status"] == "done" and fakes.cancelled == []


@pytest.mark.asyncio
async def test_a_cancel_that_wins_after_patches_were_stored_deletes_only_working_files(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    fakes.gate = asyncio.Event()
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    await asyncio.wait_for(fakes.patch_waiting.wait(), 3)

    cancelled = await manager.cancel(USER, run["id"])
    fakes.gate.set()
    await asyncio.sleep(0.1)

    assert cancelled["status"] == "cancelled"
    assert (await manager.get(USER, run["id"]))["status"] == "cancelled"
    assert len(patches(fakes)) == 1
    assert fakes.deleted == ["up1.png", "up2.png", "up3.png"] or set(fakes.deleted) == {"up1.png", "up2.png", "up3.png"}
    assert set(fakes.live()) == {"up4.png"}


@pytest.mark.asyncio
async def test_a_refused_cancel_leaves_the_run_running_and_answers_an_error(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.cancel_result = False

    with pytest.raises(DomainError) as refused:
        await manager.cancel(USER, run["id"])

    assert refused.value.code == "cancel_failed"
    assert (await manager.get(USER, run["id"]))["status"] == "running"
    assert "g1" in manager._live


@pytest.mark.asyncio
async def test_a_raising_cancel_leaves_the_run_running_and_answers_an_error(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.cancel_error = HTTPException(status_code=400, detail="cancel_failed")

    with pytest.raises(DomainError):
        await manager.cancel(USER, run["id"])

    assert (await manager.get(USER, run["id"]))["status"] == "running"


@pytest.mark.asyncio
async def test_a_refused_cancel_of_an_already_finished_generation_settles_from_its_real_state(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    fakes.cancel_error = HTTPException(status_code=400, detail="cancel_failed")

    answer = await manager.cancel(USER, run["id"])

    assert answer["status"] == "done" and len(answer["candidates"]) == 1


@pytest.mark.asyncio
async def test_stitching_that_raises_fails_the_run_and_never_escapes_the_hook(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    fakes.files["f1"].unlink()

    context = {"generation_id": "g1", "status": "completed"}
    assert manager.on_generation_complete(context) is context
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "failed" and done["error"] == NOT_PLACED and patches(fakes) == []


@pytest.mark.asyncio
async def test_a_repository_failure_ends_the_run_failed_and_never_escapes_the_hook(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    original = manager.repository.find
    calls = []

    def flaky(run_id):
        calls.append(run_id)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return original(run_id)

    manager.repository.find = flaky
    context = {"generation_id": "g1", "status": "completed"}
    assert manager.on_generation_complete(context) is context
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "failed" and done["error"]


@pytest.mark.asyncio
async def test_a_generation_with_no_run_is_untouched_by_the_hook(setup):
    manager, fakes, edit_id = setup
    await manager.start(USER, "edit_region", body(edit_id))
    context = {"generation_id": "someone-elses", "status": "completed"}

    assert manager.on_generation_complete(context) is context
    assert manager.on_generation_complete(None) is None
    await asyncio.sleep(0.05)

    assert manager._finishing == set() and patches(fakes) == []


@pytest.mark.asyncio
async def test_a_run_whose_generation_finished_while_nothing_tracked_it_is_settled_on_the_next_read(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    after_restart = restarted(manager)

    done = await after_restart.get(USER, run["id"])

    assert done["status"] == "done" and len(done["candidates"]) == 1


@pytest.mark.asyncio
async def test_a_failed_generation_is_settled_failed_after_a_restart(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.generation.status = "failed"
    fakes.generation.error_user_message = "Interrupted"

    done = await restarted(manager).get(USER, run["id"])

    assert done["status"] == "failed" and done["error"] == "Interrupted"


@pytest.mark.asyncio
async def test_a_run_whose_generation_vanished_is_failed(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.gone = True

    assert (await restarted(manager).get(USER, run["id"]))["error"] == GENERATION_GONE


@pytest.mark.asyncio
async def test_a_run_still_running_but_untracked_is_failed_as_lost(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))

    assert (await restarted(manager).get(USER, run["id"]))["error"] == RESTART_MESSAGE


@pytest.mark.asyncio
async def test_a_refused_submit_leaves_no_uploads_behind(setup):
    manager, fakes, edit_id = setup
    fakes.submit_error = DomainError("limit_exceeded", "Over your limit", 403)

    with pytest.raises(DomainError):
        await manager.start(USER, "edit_region", body(edit_id))

    assert fakes.uploads and fakes.live() == {}


@pytest.mark.asyncio
async def test_a_tag_that_cannot_be_written_cancels_the_generation_and_leaves_nothing(setup):
    manager, fakes, edit_id = setup
    fakes.tag_result = False

    with pytest.raises(DomainError) as failed:
        await manager.start(USER, "edit_region", body(edit_id))

    assert failed.value.status_code == 500 and fakes.cancelled == ["g1"]
    assert fakes.live() == {} and manager._live == {}
    assert manager.repository.by_generation("g1") is None


@pytest.mark.asyncio
async def test_a_finished_run_keeps_only_its_patches(setup):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id, candidates=2))
    assert len(fakes.live()) == 3
    complete(fakes, count=2)
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})

    done = await until_terminal(manager, USER, run["id"])

    kept = {f"{c['patch']['item_id']}.png" for c in done["candidates"]}
    assert len(done["candidates"]) == 2 and set(fakes.live()) == kept


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_a_failed_or_cancelled_run_keeps_no_files(setup, status):
    manager, fakes, edit_id = setup
    run = await manager.start(USER, "edit_region", body(edit_id))
    fakes.generation.status = status
    manager.on_generation_complete({"generation_id": "g1", "status": status})

    await until_terminal(manager, USER, run["id"])

    assert fakes.live() == {}


@pytest.mark.asyncio
async def test_a_working_file_another_running_run_still_reads_is_not_removed(setup):
    manager, fakes, edit_id = setup
    first = await manager.start(USER, "edit_region", body(edit_id))
    second = await manager.start(USER, "edit_region", body(edit_id))
    shared = set(fakes.live())
    assert len(shared) == 3

    await manager.cancel(USER, first["id"])
    assert set(fakes.live()) == shared

    await manager.cancel(USER, second["id"])
    assert fakes.live() == {}


@pytest.mark.asyncio
async def test_the_manager_sends_run_updates_to_the_owners_subscription_only(mock_db, tmp_path):
    make_users()
    hub = ConnectionHub()
    owner, stranger = AsyncMock(), AsyncMock()
    hub.active_connections = {"owner": owner, "stranger": stranger}
    manager, fakes = build_manager(tmp_path, hub=hub)
    await hub.subscribe_to_generation("owner", "g1")
    await hub.subscribe_to_generation("stranger", "g2")
    run = await manager.start(USER, "edit_region", body(make_edit().id))
    complete(fakes)
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    await until_terminal(manager, USER, run["id"])

    sent = [json.loads(call.args[0]) for call in owner.send_text.call_args_list]

    assert [m["type"] for m in sent] == [MESSAGE_TYPE, MESSAGE_TYPE]
    assert sent[-1]["run"]["id"] == run["id"] and sent[-1]["run"]["status"] == "done" and sent[-1]["generation_id"] == "g1"
    stranger.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_a_scheduling_failure_never_escapes_the_hook(setup):
    manager, fakes, edit_id = setup
    await manager.start(USER, "edit_region", body(edit_id))

    def broken(run_id, hint=None):
        raise RuntimeError("loop is closed")

    manager._schedule = broken
    context = {"generation_id": "g1", "status": "completed"}

    assert manager.on_generation_complete(context) is context


async def tab_upload_matching(manager, fakes, edit_id):
    probe = await manager.start(USER, "edit_region", body(edit_id))
    working = {name: fakes.files[name.replace(".png", "")].read_bytes() for name in fakes.live()}
    await manager.cancel(USER, probe["id"])
    kept = {}
    for index, (name, data) in enumerate(working.items()):
        kept[name] = await fakes.add_existing(data)
    return kept


@pytest.mark.asyncio
async def test_a_working_file_identical_to_an_existing_derived_upload_never_shares_its_row(setup):
    manager, fakes, edit_id = setup
    tab = await tab_upload_matching(manager, fakes, edit_id)
    run = await manager.start(USER, "edit_region", body(edit_id))

    working = {item["id"] for item in fakes.uploads if item["filename"].startswith("editor-")}
    assert {item.id for item in tab.values()}.isdisjoint(set(manager.repository.get("u1", run["id"]).crop["files"].values()))
    await manager.cancel(USER, run["id"])

    assert {item.filename for item in tab.values()} <= set(fakes.live())
    assert not {item.filename for item in tab.values()} & set(fakes.deleted)
    assert working


@pytest.mark.asyncio
async def test_a_refused_start_leaves_an_identical_existing_derived_upload_alone(setup):
    manager, fakes, edit_id = setup
    tab = await tab_upload_matching(manager, fakes, edit_id)
    fakes.submit_error = DomainError("limit_exceeded", "Over your limit", 403)

    with pytest.raises(DomainError):
        await manager.start(USER, "edit_region", body(edit_id))

    assert {item.filename for item in tab.values()} == set(fakes.live())


@pytest.mark.asyncio
async def test_a_file_a_starting_run_holds_is_not_removed_by_another_run_settling(setup):
    manager, fakes, edit_id = setup
    first = await manager.start(USER, "edit_region", body(edit_id))
    shared = set(fakes.live())
    manager._hold("up1")

    await manager.cancel(USER, first["id"])

    assert set(fakes.live()) == {"up1.png"} and shared > {"up1.png"}


@pytest.mark.asyncio
async def test_a_cancel_after_patches_never_deletes_a_patch_a_finished_run_kept(setup):
    manager, fakes, edit_id = setup
    first = await manager.start(USER, "edit_region", body(edit_id))
    complete(fakes)
    manager.on_generation_complete({"generation_id": "g1", "status": "completed"})
    kept = (await until_terminal(manager, USER, first["id"]))["candidates"][0]["patch"]["item_id"]
    second = await manager.start(USER, "edit_region", body(edit_id))
    fakes.gate = asyncio.Event()
    manager.on_generation_complete({"generation_id": "g2", "status": "completed"})
    await asyncio.wait_for(fakes.patch_waiting.wait(), 3)

    await manager.cancel(USER, second["id"])
    fakes.gate.set()
    await asyncio.sleep(0.1)

    assert f"{kept}.png" in fakes.live() and f"{kept}.png" not in fakes.deleted
    assert (await manager.get(USER, second["id"]))["status"] == "cancelled"


@pytest.mark.asyncio
async def test_a_file_two_working_pictures_share_is_removed_once_without_a_warning(setup, caplog):
    manager, fakes, edit_id = setup
    gone = set()

    def delete_upload(filename, user_id):
        if filename in gone:
            raise ValueError("Upload not found")
        gone.add(filename)
        fakes.deleted.append(filename)

    fakes.delete_upload = delete_upload
    run = await manager.start(USER, "edit_region", body(edit_id, preset="B-MASK", region=False))
    assert len(fakes.uploads) < len(manager.repository.get(USER.id, run["id"]).crop["names"])

    with caplog.at_level("WARNING"):
        await manager.cancel(USER, run["id"])

    assert fakes.live() == {}
    assert [record for record in caplog.records if "could not be removed" in record.getMessage()] == []
