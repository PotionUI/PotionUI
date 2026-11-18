import pytest
from pydantic import ValidationError

from src.features.editor_tasks import clips
from src.features.editor_tasks.registry import TaskDefinition
from src.platform.http.errors import DomainError
from tests.features.editor_tasks.clip_run_support import PRESET, build_clip_manager, clip_body
from tests.features.editor_tasks.clip_support import count_frames, frame_digests, lossless_clip
from tests.features.editor_tasks.run_support import USER, make_edit, make_users, until_terminal
from tests.platform.media_tools.media_factory import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not available")

SIZE = (64, 48)


@pytest.fixture(autouse=True)
def lossless(monkeypatch):
    monkeypatch.setattr(clips, "CRF", 0)


def build(tmp_path, task="remix", **changes):
    make_users()
    manager, fakes = build_clip_manager(tmp_path, task, **changes)
    return manager, fakes, make_edit().id


def model_clip(fakes, name, frames, size=SIZE, fps=10, pattern="mandelbrot", audio=False):
    path = fakes.root / f"{name}.mp4"
    lossless_clip(path, frames=frames, fps=fps, size=size, audio=audio, pattern=pattern)
    return path


async def settle(manager, fakes, run, result_path, file_id="r1"):
    fakes.generation.status = "completed"
    fakes.add_clip_result(file_id, result_path)
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": "completed"})
    return await until_terminal(manager, USER, run["id"])


def final_clip(fakes, done):
    return fakes.files[done["candidates"][0]["clip"]["item_id"]]


@pytest.mark.asyncio
async def test_a_whole_clip_run_sends_the_clip_itself_and_returns_the_new_clip(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id))
    sent = fakes.requests[0].form_data["clip"]
    done = await settle(manager, fakes, run, model_clip(fakes, "big", 20, size=(128, 96)))

    assert run["range"] == {"start_s": 0.0, "end_s": 2.0, "whole": True}
    assert sent.startswith("uploads/")
    assert frame_digests(fakes.upload_bytes("editor-input.mp4")) == frame_digests(fakes.files["clip"])
    candidate = done["candidates"][0]
    assert done["status"] == "done" and candidate["duration_s"] == 2.0
    assert candidate["range"] == {"start_s": 0.0, "end_s": 2.0}
    final = final_clip(fakes, done)
    assert (clips.facts_of(final).width, clips.facts_of(final).height) == (64, 48)
    assert count_frames(final) == 20 and clips.facts_of(final).has_audio


@pytest.mark.asyncio
async def test_a_whole_clip_run_of_a_resizing_task_adopts_the_size_of_the_result(mock_db, tmp_path):
    grow = TaskDefinition(id="shiny.grow", label="Grow", editors=("video",), region="none", prompt="none", range="optional", resizes=True, source="shiny")
    manager, fakes, edit_id = build(tmp_path, "shiny.grow", extra=(grow,))

    run = await manager.start(USER, "shiny.grow", clip_body(edit_id))
    done = await settle(manager, fakes, run, model_clip(fakes, "big", 20, size=(128, 96)))

    final = final_clip(fakes, done)
    assert (clips.facts_of(final).width, clips.facts_of(final).height) == (128, 96)
    assert count_frames(final) == 20 and clips.facts_of(final).has_audio


@pytest.mark.asyncio
async def test_a_range_run_cuts_the_range_sends_it_and_splices_the_result_back(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))
    original = frame_digests(fakes.files["clip"])
    sent = frame_digests(fakes.upload_bytes("editor-input.mp4"))
    result = model_clip(fakes, "model", 7, size=(128, 96), fps=24)
    done = await settle(manager, fakes, run, result)

    assert run["range"] == {"start_s": 0.5, "end_s": 1.0, "whole": False}
    assert sent == original[5:10]
    final = final_clip(fakes, done)
    spliced = frame_digests(final)
    assert len(spliced) == 20
    assert spliced[:5] == original[:5] and spliced[10:] == original[10:]
    assert spliced[5:10] != original[5:10]
    facts = clips.facts_of(final)
    assert (facts.width, facts.height, facts.fps, facts.has_audio) == (64, 48, 10.0, True)
    assert done["candidates"][0]["range"] == {"start_s": 0.5, "end_s": 1.0}
    assert done["candidates"][0]["clip"]["item_id"] and done["candidates"][0]["generation_file_id"] == "r1"


@pytest.mark.asyncio
async def test_a_range_run_keeps_the_size_even_for_a_task_that_grows_the_clip(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.0, 1.0)))
    done = await settle(manager, fakes, run, model_clip(fakes, "big", 10, size=(128, 96)))

    facts = clips.facts_of(final_clip(fakes, done))
    assert (facts.width, facts.height) == (64, 48)
    assert count_frames(final_clip(fakes, done)) == 20


@pytest.mark.asyncio
async def test_a_result_longer_or_shorter_than_the_range_is_fitted_to_it(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0), candidates=2))
    fakes.generation.status = "completed"
    fakes.add_clip_result("long", model_clip(fakes, "long", 9))
    fakes.add_clip_result("short", model_clip(fakes, "short", 3))
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": "completed"})
    done = await until_terminal(manager, USER, run["id"])

    assert [count_frames(fakes.files[item["clip"]["item_id"]]) for item in done["candidates"]] == [20, 20]


@pytest.mark.asyncio
async def test_extend_appends_after_a_whole_clip(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "extend")

    run = await manager.start(USER, "extend", clip_body(edit_id))
    original = frame_digests(fakes.files["clip"])
    joined = model_clip(fakes, "joined", 28, pattern="testsrc")
    done = await settle(manager, fakes, run, joined)

    candidate = done["candidates"][0]
    spliced = frame_digests(final_clip(fakes, done))
    assert len(spliced) == 28 and spliced[:20] == original
    assert spliced[20:] == frame_digests(joined)[20:]
    assert candidate["added"] == {"at_s": 2.0, "seconds": 0.8}
    assert candidate["duration_s"] == 2.8 and "range" not in candidate


@pytest.mark.asyncio
async def test_extend_of_a_range_inserts_the_new_time_at_the_end_of_the_range(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "extend")

    run = await manager.start(USER, "extend", clip_body(edit_id, (0.5, 1.0)))
    original = frame_digests(fakes.files["clip"])
    assert frame_digests(fakes.upload_bytes("editor-input.mp4")) == original[5:10]
    joined = model_clip(fakes, "joined", 11)
    done = await settle(manager, fakes, run, joined)

    spliced = frame_digests(final_clip(fakes, done))
    assert len(spliced) == 26
    assert spliced[:10] == original[:10]
    assert spliced[10:16] == frame_digests(joined)[5:]
    assert spliced[16:] == original[10:]
    assert done["candidates"][0]["added"] == {"at_s": 1.0, "seconds": 0.6}


@pytest.mark.asyncio
async def test_extend_takes_the_sound_of_the_new_part_from_the_result_and_keeps_the_original_elsewhere(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "extend")

    run = await manager.start(USER, "extend", clip_body(edit_id))
    done = await settle(manager, fakes, run, model_clip(fakes, "joined", 30, audio=True))

    facts = clips.facts_of(final_clip(fakes, done))
    assert facts.has_audio and abs(facts.duration - 3.0) < 0.2


@pytest.mark.asyncio
async def test_a_replacing_task_keeps_the_original_sound_whatever_the_model_made(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))
    done = await settle(manager, fakes, run, model_clip(fakes, "loud", 5, audio=True))

    facts = clips.facts_of(final_clip(fakes, done))
    assert facts.has_audio and abs(facts.duration - 2.0) < 0.15


@pytest.mark.asyncio
async def test_the_run_is_tagged_and_working_files_are_cleaned(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))
    await settle(manager, fakes, run, model_clip(fakes, "model", 5))

    assert fakes.origins["g1"] == {"kind": "editor", "edit_id": edit_id, "step_id": "s9", "task": "remix", "run_id": run["id"]}
    assert sorted(fakes.live().values()) == ["editor-clip.mp4"]


@pytest.mark.asyncio
async def test_a_run_without_a_video_result_fails_with_a_clear_reason(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.0)))
    fakes.generation.status = "completed"
    manager.on_generation_complete({"generation_id": run["generation_id"], "status": "completed"})
    done = await until_terminal(manager, USER, run["id"])

    assert done["status"] == "failed" and done["error"] == "The model returned no video"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "span, code, words",
    [
        ((5.0, 6.0), "range_outside_clip", "starts (5s) past the end"),
        ((1.0, 9.0), "range_outside_clip", "ends (9s) past the end"),
        ((1.0, 1.01), "range_invalid", "shorter than one frame"),
    ],
)
async def test_a_range_outside_the_clip_is_refused_before_anything_is_started(mock_db, tmp_path, span, code, words):
    manager, fakes, edit_id = build(tmp_path, "remix")

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remix", clip_body(edit_id, span))

    assert refused.value.code == code and words in refused.value.message and refused.value.status_code == 422
    assert fakes.requests == [] and fakes.live() == {}


@pytest.mark.parametrize("span", [(1.0, 1.0), (1.5, 1.0), (-1.0, 1.0)])
def test_a_range_that_ends_before_it_starts_is_refused_by_the_request_itself(span):
    with pytest.raises(ValidationError):
        clip_body("ed", span)


@pytest.mark.asyncio
async def test_a_task_the_mode_does_not_serve_is_not_offered(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "extend", clip_body(edit_id))

    assert refused.value.code == "task_not_offered" and fakes.requests == []


@pytest.mark.asyncio
async def test_a_range_on_a_picture_task_is_refused(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")
    body = clip_body(edit_id, (0.5, 1.0), preset="A-CROP")

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "edit_region", body)

    assert refused.value.code == "range_not_supported" and fakes.requests == []


@pytest.mark.asyncio
async def test_a_picture_mode_cannot_serve_a_video_task(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remix", clip_body(edit_id, preset="A-CROP"))

    assert refused.value.code == "task_not_offered" and fakes.requests == []


@pytest.mark.asyncio
async def test_the_preset_is_the_video_one_in_these_tests(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body(edit_id))

    assert fakes.requests[0].preset_id == PRESET and fakes.requests[0].mode == "editor:edit"
    assert run["task"] == "remix"


@pytest.mark.asyncio
async def test_a_clip_run_needs_no_saved_edit(mock_db, tmp_path):
    manager, fakes, _edit_id = build(tmp_path, "remix")

    run = await manager.start(USER, "remix", clip_body("video-tab-1"))

    assert run["edit_id"] == "video-tab-1"



@pytest.mark.asyncio
async def test_a_range_over_the_cap_of_the_mode_is_refused_with_the_cap_in_the_message(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix", max_range_s=1.0)

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remix", clip_body(edit_id, (0.0, 1.5)))

    assert refused.value.code == "range_too_long" and refused.value.status_code == 422
    assert refused.value.message == "Remix works on up to 1 s; mark a shorter section"
    assert fakes.requests == []


@pytest.mark.asyncio
async def test_the_whole_clip_is_refused_when_it_is_longer_than_the_cap(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix", max_range_s=1.0)

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remix", clip_body(edit_id))

    assert refused.value.code == "range_too_long" and fakes.requests == []


@pytest.mark.asyncio
async def test_the_whole_clip_is_allowed_when_it_is_within_the_cap(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix", max_range_s=2.0)

    run = await manager.start(USER, "remix", clip_body(edit_id))

    assert run["range"]["whole"] is True and len(fakes.requests) == 1


@pytest.mark.asyncio
async def test_a_range_exactly_at_the_cap_runs_and_the_original_sound_is_kept(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix", max_range_s=1.0)

    run = await manager.start(USER, "remix", clip_body(edit_id, (0.5, 1.5)))
    done = await settle(manager, fakes, run, model_clip(fakes, "model", 10, audio=False))

    final = final_clip(fakes, done)
    assert done["status"] == "done" and count_frames(final) == 20
    assert clips.facts_of(final).has_audio


@pytest.mark.asyncio
async def test_a_mode_without_a_cap_takes_any_range(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")

    await manager.start(USER, "remix", clip_body(edit_id, (0.0, 2.0)))

    assert len(fakes.requests) == 1


@pytest.mark.asyncio
async def test_remix_needs_a_prompt(mock_db, tmp_path):
    manager, fakes, edit_id = build(tmp_path, "remix")
    body = clip_body(edit_id, (0.5, 1.0), prompt="  ")

    with pytest.raises(DomainError) as refused:
        await manager.start(USER, "remix", body)

    assert refused.value.code == "prompt_required" and fakes.requests == []
