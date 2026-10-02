import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

pytest.importorskip("cv2", reason="cv2 is needed to write and read the tiny test clips", exc_type=ImportError)

from src.features.cloud.cost_repository import GenerationCostRepository
from src.features.cloud.testing.fake import FakeBehaviour, fake_director_specs, fake_specs
from src.features.generation.repository import generation_repo
from src.features.generation.status_tracker import GenerationState
from src.features.video_director import VideoDirectorValidationError
from src.pipelines.outputs import ProgressGenerationOutput
from tests.features.cloud.cloud_generation_harness import USER_ID, CloudGeneration

SOURCE = Path("content/plugins/marketplace/openrouter-provider/presets/VideoGeneration/standard")
DIRECTOR_PRESET_ID = "01KCLOUDDIRECTORPRESET000A"


def add_user(db):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (USER_ID, USER_ID, f"{USER_ID}@example.test"),
        )


class DirectorGeneration(CloudGeneration):
    def __init__(self, tmp_path: Path, behaviour: FakeBehaviour, provider_model: str) -> None:
        super().__init__(tmp_path, behaviour, provider_model=provider_model)
        target = self.presets / "cloud" / "Director" / "v1"
        shutil.copytree(SOURCE, target)
        preset = (target / "preset.yml").read_text(encoding="utf-8")
        preset = preset.replace('id: "01M3SV8HJTGT3JPPH2BA5MP6PR"', f'id: "{DIRECTOR_PRESET_ID}"')
        (target / "preset.yml").write_text(preset.replace("cloud.openrouter", "cloud.fake"), encoding="utf-8")

    def request(self, prompt: str = "", quantity: int = 1, mode: str = "txt2video", **form: Any) -> Any:
        request = super().request(prompt=prompt, quantity=quantity, mode=mode, **form)
        request.preset_id = DIRECTOR_PRESET_ID
        request.form_data.pop("quantity", None)
        return request


@pytest.fixture
async def make(mock_db, tmp_path):
    add_user(mock_db)

    async def build(provider_model="fake/director-1", **behaviour):
        behaviour = FakeBehaviour(
            mode="sync", real_video=True, strict_capabilities=True, specs=[*fake_specs(), *fake_director_specs()], **behaviour,
        )
        return await DirectorGeneration(tmp_path, behaviour, provider_model).start()

    return build


def director(*frames, fps=24, media=()):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": fps, "seed": 40, "continuation": {"source": "last_frame", "overlap_frames": 0, "stitch": True}},
        "segments": [{"id": f"s{i}", "prompt": f"shot {i}", "frames": count} for i, count in enumerate(frames)],
        "media": list(media),
    }


def saved(generation_id="gen-1"):
    return generation_repo.get_files(generation_id, is_final=True)


async def test_a_three_shot_film_runs_one_paid_request_per_shot_and_saves_the_shots_and_the_film(make, mock_db):
    run = await make()

    record = await run.run(video_director=director(48, 96, 48), aspect_ratio="16:9")

    assert record.state == GenerationState.COMPLETED
    requests = run.behaviour.requests
    assert [(r.task, r.params.get("duration_s"), r.seed, r.prompt) for r in requests] == [
        ("txt2video", 2, 40, "shot 0"),
        ("img2video", 4, 41, "shot 1"),
        ("img2video", 2, 42, "shot 2"),
    ]
    assert [list(r.inputs) for r in requests] == [[], ["first_frame"], ["first_frame"]]
    assert all(r.params.get("aspect_ratio") == "16:9" for r in requests)
    files = saved()
    assert len(files) == 4 and all((run.storage / file.file_path).is_file() for file in files)
    assert len(GenerationCostRepository().for_generation("gen-1")) == 3
    segments = {output.segment_id for output in run.collected.of(ProgressGenerationOutput) if output.segment_id}
    assert segments == {"s0", "s1", "s2"}


async def test_a_single_shot_carries_its_start_and_end_pictures_to_the_provider(make, mock_db):
    run = await make()
    for name, colour in (("start.png", (200, 0, 0)), ("end.png", (0, 0, 200))):
        Image.new("RGB", (8, 8), colour).save(run.storage / name)
    document = {
        "schema_version": 1, "mode": "flf", "settings": {"fps": 24, "duration": 6, "seed": 9},
        "segments": [{"id": "seg-0", "prompt": "a door opens"}],
        "media": [
            {"id": "m1", "role": "first", "segment_id": "seg-0", "media": {"path": "start.png", "type": "image"}},
            {"id": "m2", "role": "last", "segment_id": "seg-0", "media": {"path": "end.png", "type": "image"}},
        ],
    }

    record = await run.run(mode="img2video", video_director=document)

    assert record.state == GenerationState.COMPLETED
    (request,) = run.behaviour.requests
    assert request.task == "img2video" and request.params["duration_s"] == 6
    assert sorted(request.inputs) == ["first_frame", "last_frame"]
    assert len(saved()) == 1


async def test_a_length_the_model_does_not_make_is_refused_before_anything_is_paid(make):
    run = await make()

    with pytest.raises(VideoDirectorValidationError) as raised:
        await run.submit(video_director=director(48, 60))

    assert "is not a length this model makes" in str(raised.value)
    assert run.behaviour.requests == []


async def test_a_text_only_model_refuses_a_start_picture(make):
    run = await make(provider_model="fake/director-text-1")
    Image.new("RGB", (8, 8)).save(run.storage / "start.png")
    media = [{"id": "m1", "role": "first", "segment_id": "s0", "media": {"path": "start.png", "type": "image"}}]

    with pytest.raises(VideoDirectorValidationError):
        await run.submit(video_director=director(120, media=media))

    assert run.behaviour.requests == []


async def test_a_failing_shot_keeps_the_finished_shots_and_names_the_shot(make, mock_db):
    run = await make(fail_from_submit=3, fail_kind="credits")

    record = await run.run(video_director=director(48, 48, 48, 48))

    assert record.state == GenerationState.FAILED
    assert record.error.startswith("Shot 3 of 4 failed. Shots 1-2 were kept.")
    assert len(run.behaviour.requests) == 3
    assert len(saved()) == 2


def shot_messages(run):
    from src.features.generation.output_serializer import GenerationOutputSerializer
    from src.pipelines.outputs import DirectorShotGenerationOutput

    serializer = GenerationOutputSerializer(generation_id="gen-1")
    return [serializer.serialize_output(item) for item in run.collected.of(DirectorShotGenerationOutput)]


async def test_every_shot_change_reaches_subscribers_with_the_saved_clips_address(make, mock_db):
    run = await make()

    await run.run(video_director=director(48, 48))

    messages = shot_messages(run)
    assert all(message["type"] == "director_shot_update" for message in messages)
    assert [(m["shot_id"], m["status"]) for m in messages if m["status"] != "generating"] == [
        ("s0", "queued"), ("s1", "queued"), ("s0", "done"), ("s1", "done"),
    ]
    done = [m for m in messages if m["status"] == "done"]
    files = {file.file_path for file in saved()}
    assert all(m["output_path"] in files for m in done)
    assert all(m["output_url"] == f"/api/media/generations/gen-1/{m['output_path'].rsplit('/', 1)[-1]}" for m in done)


async def test_a_failure_on_shot_two_marks_it_failed_and_skips_the_rest(make, mock_db):
    run = await make(fail_from_submit=2, fail_kind="refused")

    record = await run.run(video_director=director(48, 48, 48))

    assert record.state == GenerationState.FAILED
    statuses = [(m["shot_id"], m["status"]) for m in shot_messages(run) if m["status"] != "generating"]
    assert statuses[-3:] == [("s0", "done"), ("s1", "failed"), ("s2", "skipped")]
    (failed,) = [m for m in shot_messages(run) if m["status"] == "failed"]
    assert failed["message"] == "Fake refusal" and failed["output_url"] is None
    assert len(saved()) == 1


async def test_a_retry_starts_its_first_shot_from_the_previous_shots_saved_clip(make, mock_db):
    run = await make()
    await run.run(video_director=director(48, 48))
    first_clip = next(m["output_path"] for m in shot_messages(run) if m["status"] == "done")
    run.behaviour.requests.clear()
    retry = {
        **director(48, 48, 48),
        "media": [{"id": "m1", "role": "first", "segment_id": "s1", "media": {"relative_path": first_clip, "type": "video"}}],
        "render": {"scope": "shots", "shot_ids": ["s1", "s2"]},
    }
    run.collected = type(run.collected)()
    run.generation_id = "gen-2"

    record = await run.run(generation_id="gen-2", video_director=retry)

    assert record.state == GenerationState.COMPLETED
    first, second = run.behaviour.requests
    assert (first.task, first.prompt, sorted(first.inputs)) == ("img2video", "shot 1", ["first_frame"])
    assert first.inputs["first_frame"][0].path.suffix == ".png"
    assert (second.task, second.prompt) == ("img2video", "shot 2")
    assert len(saved("gen-2")) == 3
