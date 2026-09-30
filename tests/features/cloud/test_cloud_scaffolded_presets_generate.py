import pytest
from PIL import Image

from src.features.cloud.testing.fake import FakeBehaviour
from src.features.forms.binding import FormBindingError
from src.features.generation.model_repository import generation_model_repo
from src.features.generation.repository import generation_repo
from src.features.generation.status_tracker import GenerationState
from tests.features.cloud.cloud_generation_harness import USER_ID, CloudGeneration

IMAGE = "fake/image-1"
VIDEO = "fake/video-1"
MODES = ["txt2img", "edit", "txt2video", "img2video"]


def add_user(db):
    with db.get_cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, email, password_hash, account_type) VALUES (?, ?, ?, 'x', 'USER')",
            (USER_ID, USER_ID, f"{USER_ID}@example.test"),
        )


@pytest.fixture
async def make(mock_db, tmp_path):
    add_user(mock_db)

    async def build(provider_model=IMAGE, **behaviour):
        return await CloudGeneration(
            tmp_path, FakeBehaviour(mode="sync", **behaviour), scaffold_modes=MODES, provider_model=provider_model
        ).start()

    return build


def upload(run, name="ref.png"):
    folder = run.storage / "uploads"
    folder.mkdir(exist_ok=True)
    path = folder / name
    Image.new("RGB", (8, 8), (10, 20, 30)).save(path)
    return f"uploads/{name}"


def cloud_pipe(run):
    return next(pipe for pipe in run.prepared[-1] if pipe["name"] == "cloud_generate")


async def test_a_scaffolded_text_to_image_preset_generates_and_sends_only_what_the_model_supports(make):
    run = await make()

    record = await run.run(
        prompt="a cat",
        aspect_ratio="16:9",
        quality=7,
        resolution="4K",
        output_format="png",
        provider_options={"x.style": "noir", "x.unknown": 1},
    )

    assert record.state == GenerationState.COMPLETED
    (request,) = run.behaviour.requests
    assert request.task == "txt2img"
    assert request.params == {"aspect_ratio": "16:9", "quality": 7, "background": False, "x.style": "noir"}
    assert [model.filename for model in generation_model_repo.get_by_generation("gen-1")] == [run.slug]
    assert len(generation_repo.get_files("gen-1", is_final=True)) == 1


async def test_nothing_unsupported_reaches_the_pipe_config_or_the_provider(make):
    run = await make()

    await run.run(resolution="4K", output_format="png", strength=0.5)

    config = cloud_pipe(run)["config"]
    assert config["task"] == "txt2img"
    assert config["params"]["resolution"] is None
    assert config["params"]["output_format"] is None
    (request,) = run.behaviour.requests
    assert set(request.params) <= {"aspect_ratio", "quality", "background", "x.style"}


async def test_a_value_the_model_does_not_offer_is_refused_before_anything_is_sent(make):
    run = await make()

    with pytest.raises(FormBindingError) as refused:
        await run.submit(aspect_ratio="21:9")

    assert "not offered by this model" in str(refused.value)
    assert run.behaviour.requests == []


async def test_a_scaffolded_edit_preset_sends_its_reference_pictures(make):
    run = await make()

    record = await run.run(mode="edit", references=[upload(run, "a.png"), upload(run, "b.png")])

    assert record.state == GenerationState.COMPLETED
    (request,) = run.behaviour.requests
    assert request.task == "img_edit"
    assert [media.path.name for media in request.inputs["reference"]] == ["images_0.png", "images_1.png"]
    assert "strength" not in request.params


async def test_a_scaffolded_text_to_video_preset_sends_the_duration(make):
    run = await make(provider_model=VIDEO)

    record = await run.run(mode="txt2video", duration=6, generate_audio=True, aspect_ratio="16:9")

    (request,) = run.behaviour.requests
    assert request.task == "txt2video"
    assert request.params == {"duration_s": 6, "generate_audio": True}
    assert record.state == GenerationState.COMPLETED


async def test_a_scaffolded_image_to_video_preset_sends_the_start_frame(make):
    run = await make(provider_model=VIDEO)

    record = await run.run(mode="img2video", first_frame=upload(run, "start.png"), duration=4)

    (request,) = run.behaviour.requests
    assert request.task == "img2video"
    assert list(request.inputs) == ["first_frame"]
    assert record.state == GenerationState.COMPLETED


async def test_a_scaffolded_image_to_video_preset_sends_both_frames_under_their_roles(make):
    run = await make(provider_model=VIDEO)

    record = await run.run(
        mode="img2video", first_frame=upload(run, "start.png"), last_frame=upload(run, "end.png"), duration=4
    )

    (request,) = run.behaviour.requests
    assert record.state == GenerationState.COMPLETED
    assert sorted(request.inputs) == ["first_frame", "last_frame"]
    assert [media.path.name for media in request.inputs["first_frame"]] == ["first_frame_0.png"]
    assert [media.path.name for media in request.inputs["last_frame"]] == ["last_frame_0.png"]
