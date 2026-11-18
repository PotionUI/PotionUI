import pytest

from src.features.editor_tasks import clips
from src.features.edits.manager import EditManager
from src.features.edits.repository import EditRepository
from src.features.media.upload_repository import UploadRepository
from src.platform.http.errors import NotFoundError
from tests.features.editor_tasks.clip_run_support import build_clip_manager, clip_body
from tests.features.editor_tasks.run_support import USER, make_users
from tests.platform.media_tools.media_factory import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not available")



@pytest.fixture(autouse=True)
def lossless(monkeypatch):
    monkeypatch.setattr(clips, "CRF", 0)


CLIP_STATE = {"v": 1, "stack": {"steps": [], "cursor": 0}, "clip": {"duration_seconds": 2, "width": 64, "height": 48}}


def clip_edit(user_id):
    return EditManager(EditRepository(), UploadRepository()).create(
        user_id, "video", None, "library", "clip", CLIP_STATE
    ).id


@pytest.mark.asyncio
async def test_a_clip_run_started_from_a_saved_clip_edit_is_tagged_with_that_edit(mock_db, tmp_path):
    make_users()
    manager, fakes = build_clip_manager(tmp_path, "remix")
    edit_id = clip_edit(USER.id)

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))

    assert run["edit_id"] == edit_id
    assert fakes.origins["g1"]["edit_id"] == edit_id


@pytest.mark.asyncio
@pytest.mark.parametrize("make", ["someone else's edit", "the old per-tab id", "a missing edit"])
async def test_a_clip_run_without_an_edit_of_the_caller_is_refused_and_leaves_nothing(mock_db, tmp_path, make):
    make_users()
    manager, fakes = build_clip_manager(tmp_path, "remix")
    edit_id = {
        "someone else's edit": lambda: clip_edit("u2"),
        "the old per-tab id": lambda: "video-tab-1",
        "a missing edit": lambda: "ed_missing",
    }[make]()

    with pytest.raises(NotFoundError) as refused:
        await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))

    assert refused.value.code == "edit_not_found"
    assert not fakes.origins
    assert fakes.uploads == []
