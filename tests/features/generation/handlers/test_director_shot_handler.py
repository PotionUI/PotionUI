from pathlib import Path

from src.features.generation.handlers.director_shot_handler import serialize_director_shot_output
from src.features.generation.output_serializer import GenerationOutputSerializer
from src.features.generation.output_types import SerializeContext, output_type_registry
from src.pipelines.outputs import DirectorShotGenerationOutput, VideoGenerationOutput


def saved_video(path="generations/2026-10-02/gen-1/2_01ABC.mp4"):
    video = VideoGenerationOutput(video_path=Path("/tmp/shot.mp4"), temporary=False, seed=4)
    video._saved_path = path
    return video


def test_a_finished_shot_is_sent_as_a_director_shot_update_with_its_saved_clip():
    output = DirectorShotGenerationOutput(
        shot_id="s1", status="done", shot_index=1, shot_count=3, progress=1.0, video=saved_video(), pipe_id=4,
    )

    message = GenerationOutputSerializer(generation_id="gen-1").serialize_output(output)

    assert message["type"] == "director_shot_update" and message["generation_id"] == "gen-1" and message["pipe_id"] == 4
    assert (message["shot_id"], message["status"], message["shot_index"], message["shot_count"], message["progress"]) == (
        "s1", "done", 1, 3, 1.0,
    )
    assert message["output_url"] == "/api/media/generations/gen-1/2_01ABC.mp4"
    assert message["output_path"] == "generations/2026-10-02/gen-1/2_01ABC.mp4"
    assert message["message"] is None and message["nsfw"] is False


def test_a_failed_shot_carries_its_plain_reason_and_no_clip():
    output = DirectorShotGenerationOutput(shot_id="s2", status="failed", message="Out of credits")

    data = serialize_director_shot_output(output, SerializeContext(generation_id="gen-1"))

    assert (data["status"], data["message"], data["output_url"], data["output_path"]) == ("failed", "Out of credits", None, None)


def test_a_clip_that_was_not_saved_or_whose_preview_is_suppressed_gets_no_address():
    unsaved = VideoGenerationOutput(video_path=Path("/tmp/shot.mp4"), temporary=False)
    suppressed = saved_video()
    suppressed._preview_suppressed = True
    flagged = saved_video()
    flagged._content_nsfw = True
    ctx = SerializeContext(generation_id="gen-1")

    assert serialize_director_shot_output(DirectorShotGenerationOutput("s", "done", video=unsaved), ctx)["output_url"] is None
    assert serialize_director_shot_output(DirectorShotGenerationOutput("s", "done", video=suppressed), ctx)["output_url"] is None
    nsfw = serialize_director_shot_output(DirectorShotGenerationOutput("s", "done", video=flagged), ctx)
    assert nsfw["nsfw"] is True and nsfw["output_url"] is not None


def test_the_shot_update_is_registered_as_a_serialize_only_output_type():
    spec = output_type_registry.spec_for(DirectorShotGenerationOutput(shot_id="s", status="queued"))

    assert (spec.key, spec.message_type, spec.handler_cls, spec.server_only) == ("director_shot", "director_shot_update", None, False)
