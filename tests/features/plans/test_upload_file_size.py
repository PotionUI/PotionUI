from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.features.media.editing.dto import CropOperation, TrimOperation
from src.features.media.routes import MediaController
from src.features.plans.errors import LimitExceeded
from src.features.plans.kinds import human_bytes
from src.features.user_groups.constants import ALL_USERS_GROUP_ID
from src.platform.plugins.limit_kinds import AdmissionRequest, InvalidLimitKindError, LimitKind, validate_kind
from tests.features.plans.conftest import make_plan
from tests.features.plans.test_enforcement import (
    backend,
    bind_form_passthrough,
    editor,
    history_archive,
    history_file,
    limit_storage,
    make_request,
    media_store,
    orchestrator_for,
    repo,
    splits_into,
    stored_media,
    stored_uploads,
    upload_rows,
    writes,
)

MB = 1024 ** 2


def limit_files(plans, value, **more):
    plan = make_plan(plans, "Files", upload_file_size=value, **more)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)
    return plan


def upload(size=None, items=None, user_id="u1"):
    return AdmissionRequest(point="upload", user_id=user_id, incoming_bytes=size, item_bytes=items)


@pytest.mark.parametrize("value,text", [
    (500, "500 B"), (1024, "1 KB"), (3277, "3.2 KB"), (120 * MB, "120 MB"), (50 * MB, "50 MB"), (1024 ** 3, "1 GB"),
])
def test_sizes_read_in_the_largest_whole_unit(value, text):
    assert human_bytes(value) == text


def test_the_kind_is_a_per_file_byte_limit_enforced_only_at_upload(plans):
    described = {k["key"]: k for k in plans.manager.kinds()["kinds"]}["upload_file_size"]

    assert described["per_item"] is True
    assert (described["value_type"], described["unit"], described["input_scale"]) == ("bytes", "MB", MB)
    assert described["enforce_at"] == ["upload"]
    assert {k["key"]: k["per_item"] for k in plans.manager.kinds()["kinds"]}["storage_bytes"] is False


def test_a_per_item_kind_must_be_a_byte_level_without_a_ledger():
    base = dict(key="x_size", label="X", value_type="bytes", enforce_at=("upload",), per_item=True)
    validate_kind(LimitKind(**base))
    for broken in ({"ledger": True, "window": "day"}, {"window": "day"}, {"value_type": "count"}):
        with pytest.raises(InvalidLimitKindError):
            validate_kind(LimitKind(**{**base, **broken}))
    with pytest.raises(InvalidLimitKindError):
        validate_kind(LimitKind(**{**base, "per_item": False}))


def test_a_file_over_the_limit_is_refused_with_its_size_and_the_limit(seed, plans, guard):
    seed.user("u1")
    seed.upload("u1", 900 * MB)
    limit_files(plans, 50 * MB)

    guard.admit(upload(50 * MB))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(upload(120 * MB))

    payload = refused.value.payload()
    assert payload["error"] == "limit_exceeded"
    assert (payload["kind"], payload["code"], payload["point"]) == ("upload_file_size", "upload_file_size_exceeded", "upload")
    assert (payload["used"], payload["incoming"], payload["limit"]) == (120 * MB, 120 * MB, 50 * MB)
    assert payload["resets_at"] is None
    assert payload["message"] == (
        "This file is 120 MB; your plan allows files up to 50 MB. Ask your admin for more."
    )


def test_each_file_is_measured_alone_not_the_total(seed, plans, guard):
    seed.user("u1")
    limit_files(plans, 10)

    guard.admit(upload(30, items=(10, 10, 10)))
    with pytest.raises(LimitExceeded) as refused:
        guard.admit(upload(15, items=(4, 11)))

    assert refused.value.payload()["incoming"] == 11
    guard.admit(upload(999, items=()))


def test_a_full_storage_and_a_too_big_file_name_the_file_first(seed, plans, guard):
    seed.user("u1")
    seed.upload("u1", 100)
    plan = make_plan(plans, "Both", storage_bytes=100, upload_file_size=10)
    plans.guard.plans.set_group_plan(ALL_USERS_GROUP_ID, plan.id)

    with pytest.raises(LimitExceeded) as refused:
        guard.admit(upload(20))

    assert refused.value.kinds == ["upload_file_size", "storage_bytes"]
    assert refused.value.payload()["code"] == "upload_file_size_exceeded"


def test_a_submit_is_never_refused_by_the_file_limit(seed, plans, guard):
    seed.user("u1")
    limit_files(plans, 0)

    guard.admit(AdmissionRequest(point="submit", user_id="u1", ref_id="g1"))
    guard.check(upload())


def test_check_items_asks_only_the_file_limit(seed, plans, guard):
    seed.user("u1")
    seed.upload("u1", 100)
    limit_files(plans, 10, storage_bytes=100)

    guard.check_items(upload(10))
    with pytest.raises(LimitExceeded) as refused:
        guard.check_items(upload(11))
    assert refused.value.kinds == ["upload_file_size"]


def test_admins_are_exempt_from_the_file_limit(seed, plans, guard):
    seed.user("admin", admin=True)
    limit_files(plans, 1)

    guard.admit(upload(10 * MB, user_id="admin"))


def test_the_most_generous_group_file_limit_wins_and_a_group_without_it_lifts_it(seed, plans, guard):
    seed.user("u1")
    limit_files(plans, 1 * MB)
    small, large, open_group = seed.group("small"), seed.group("large"), seed.group("open")
    plans.guard.plans.set_group_plan(small, make_plan(plans, "Small", upload_file_size=50 * MB).id)
    plans.guard.plans.set_group_plan(large, make_plan(plans, "Large", upload_file_size=200 * MB).id)
    plans.guard.plans.set_group_plan(open_group, make_plan(plans, "Open", storage_bytes=10 ** 12).id)
    seed.join(small, "u1")
    seed.join(large, "u1")

    assert guard.resolution("u1").value("upload_file_size") == 200 * MB
    guard.admit(upload(200 * MB))
    with pytest.raises(LimitExceeded):
        guard.admit(upload(200 * MB + 1))

    seed.join(open_group, "u1")
    assert guard.resolution("u1").value("upload_file_size") is None
    guard.admit(upload(10 ** 10))


@pytest.mark.asyncio
async def test_the_media_upload_refuses_a_big_file_before_dedupe_or_any_write(seed, plans, tmp_path):
    seed.user("u1")
    limit_files(plans, 2)
    store, driver = media_store(tmp_path, plans.guard)

    with pytest.raises(LimitExceeded):
        await store.upload_media(b"abc", "a.png", "image/png", user_id="u1")

    store.upload_repo.find_by_hash.assert_not_called()
    driver.put_bytes.assert_not_called()


def test_only_library_uploads_are_held_to_the_file_limit(seed, plans, tmp_path):
    seed.user("u1")
    limit_files(plans, 2)
    store, driver = media_store(tmp_path, plans.guard)

    store.check_upload_size("u1", 3, "derived_artifact")
    store.check_upload_size("u1", None, "user_upload")
    store.check_upload_size("u1", 2, "user_upload")
    with pytest.raises(LimitExceeded):
        store.check_upload_size("u1", 3, "user_upload")


@pytest.mark.asyncio
async def test_the_upload_route_refuses_by_the_streamed_size_before_reading_the_file(seed, plans, tmp_path):
    seed.user("u1")
    limit_files(plans, 1024)
    store, driver = media_store(tmp_path, plans.guard)
    file = Mock(filename="a.png", content_type="image/png", size=3277, read=AsyncMock(return_value=b"x" * 3277))

    with pytest.raises(HTTPException) as raised:
        await MediaController(store, Mock()).upload_media(file, Mock(id="u1"))

    assert raised.value.status_code == 403
    assert raised.value.detail["kind"] == "upload_file_size"
    assert raised.value.detail["message"].startswith("This file is 3.2 KB; your plan allows files up to 1 KB.")
    file.read.assert_not_awaited()
    driver.put_bytes.assert_not_called()


@pytest.mark.asyncio
async def test_the_history_upload_checks_each_file_before_anything_is_written(seed, plans):
    seed.user("u1")
    limit_files(plans, 4)
    archive, generation_repo, driver = history_archive(plans.guard)

    with pytest.raises(LimitExceeded) as refused:
        await archive.upload_generations([history_file(b"abc"), history_file(b"abcde")], [], "u1")

    assert refused.value.payload()["incoming"] == 5
    generation_repo.create.assert_not_called()
    driver.put_bytes.assert_not_called()

    files = [history_file(b"abcd"), history_file(b"abcd"), history_file(b"x" * 99, content_type="text/plain")]
    result = await archive.upload_generations(files, [], "u1")
    assert len(result["files"]) == 2


@pytest.mark.asyncio
async def test_an_editor_save_as_new_over_the_file_limit_is_refused_and_leaves_no_file(editor, plans, seed):
    manager, image, driver, size = editor
    video = stored_media(driver, manager.upload_repo, "clip.mp4", "video")
    limit_files(plans, 8)
    trim = [TrimOperation(type="trim", start_seconds=0, end_seconds=1)]

    with patch("src.features.media.editing.editor.apply_video_operations", writes(9)):
        with pytest.raises(LimitExceeded) as refused:
            await manager.edit_item(video.id, "u1", trim, mode="new")

    assert refused.value.payload()["code"] == "upload_file_size_exceeded"
    assert stored_uploads(driver) == ["clip.mp4", "source.png"]
    assert len(upload_rows(seed)) == 2

    with patch("src.features.media.editing.editor.apply_video_operations", writes(8)):
        await manager.edit_item(video.id, "u1", trim, mode="new")
    assert len(stored_uploads(driver)) == 3


@pytest.mark.asyncio
async def test_an_editor_replace_is_not_a_new_file(editor, plans):
    manager, image, driver, size = editor
    limit_files(plans, 1)

    result = await manager.edit_item(image.id, "u1", [CropOperation(type="crop", x=0, y=0, width=150, height=90)],
                                     mode="replace")

    assert result.replaced is True


@pytest.mark.asyncio
async def test_a_frame_grab_over_the_file_limit_is_refused_and_leaves_no_file(editor, plans):
    manager, image, driver, size = editor
    video = stored_media(driver, manager.upload_repo, "clip.mp4", "video")
    limit_files(plans, 50)

    with patch("src.features.media.editing.editor.extract_video_frame", writes(51)):
        with pytest.raises(LimitExceeded):
            await manager.extract_frame(video.id, "u1", 1.0)

    assert stored_uploads(driver) == ["clip.mp4", "source.png"]


@pytest.mark.asyncio
async def test_an_audio_split_is_checked_part_by_part(editor, plans):
    manager, image, driver, size = editor
    audio = stored_media(driver, manager.upload_repo, "song.mp3", "audio")
    limit_files(plans, 10)

    with patch("src.features.media.editing.editor.split_audio", splits_into(10, 11)):
        with pytest.raises(LimitExceeded):
            await manager.split_item(audio.id, "u1", 5)
    assert stored_uploads(driver) == ["song.mp3", "source.png"]

    with patch("src.features.media.editing.editor.split_audio", splits_into(10, 10, 10)):
        parts = await manager.split_item(audio.id, "u1", 5)
    assert len(parts) == 3


def test_library_copy_and_inspiration_save_check_each_file(seed, plans, tmp_path):
    from src.features.inspirations.operations.saves import save_to_library
    from src.features.library.operations.mutations import copy_generation_file

    seed.user("u1")
    limit_files(plans, 4)
    source = tmp_path / "out.png"
    source.write_bytes(b"12345")
    driver = Mock()
    library = SimpleNamespace(
        file_repository=Mock(get_by_id=Mock(return_value=Mock(file_type="IMAGE", file_path="out.png", file_size=5))),
        file_store=Mock(get_full_path=Mock(return_value=str(source)), base_storage_dir=str(tmp_path)),
        file_resolver=Mock(validate_path_security=Mock(return_value=True)),
        storage_driver=driver,
        limit_guard=plans.guard,
    )
    inspirations = SimpleNamespace(
        repository=Mock(get_by_id=Mock(return_value=Mock(media=[
            {"filename": "a.png", "file_size": 3}, {"filename": "b.png", "file_size": 5},
        ]))),
        file_store=library.file_store,
        file_resolver=library.file_resolver,
        storage_driver=driver,
        limit_guard=plans.guard,
    )

    with pytest.raises(LimitExceeded):
        copy_generation_file(library, "f1", "u1")
    with pytest.raises(LimitExceeded) as refused:
        save_to_library(inspirations, "i1", "u1")

    assert refused.value.payload()["incoming"] == 5
    driver.put_file.assert_not_called()


@pytest.mark.asyncio
async def test_a_generation_submit_ignores_the_file_limit(seed, plans, repo, backend):
    seed.user("u1")
    limit_files(plans, 0)

    await orchestrator_for(backend, plans.guard).start_generation(make_request(), "u1")

    repo.create.assert_called_once()


def test_storage_limits_still_use_the_total_of_every_file(seed, plans, guard):
    seed.user("u1")
    limit_storage(plans, 10)

    with pytest.raises(LimitExceeded) as refused:
        guard.admit(upload(11, items=(4, 7)))

    assert refused.value.payload()["code"] == "storage_quota_exceeded"


def test_the_impact_preview_never_calls_anyone_over_a_file_limit(seed, plans, manager):
    seed.user("u1")
    files = make_plan(plans, "Files", upload_file_size=0)

    impact = manager.impact(ALL_USERS_GROUP_ID, files.id)

    assert impact["summary"]["over_after"] == 0
    assert impact["members"][0]["limits"][0]["change"] == "added"


def test_an_account_without_a_plan_subject_still_reports_storage(manager):
    data = manager.my_limits(SimpleNamespace(id="ghost"))

    assert data["limits"] == []
    assert data["usage"] == {"storage_bytes": 0}
