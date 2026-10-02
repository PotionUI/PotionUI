from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

pytest.importorskip("cv2", reason="cv2 is needed to write and read the tiny test clips", exc_type=ImportError)

from src.features.cloud.testing.fake import fake_video_bytes, image_colour
from src.pipelines.cloud import CloudRunArtifact, CloudRunCancelled, CloudRunCost, CloudRunError, CloudRunOutcome, CloudRunProgress
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    CostGenerationOutput,
    DirectorShotGenerationOutput,
    GalleryGenerationOutput,
    ProgressGenerationOutput,
)
from src.pipelines.pipes._shared.media.frame_extract import extract_frame
from src.pipelines.pipes.cloud_generate import main as pipe_module
from src.pipelines.pipes.cloud_generate.director import plan_shots
from src.pipelines.pipes.cloud_generate.main import CloudGeneratePipe

RED = (220, 30, 30)
BLUE = (30, 30, 220)


def picture(path: Path, colour) -> Path:
    Image.new("RGB", (16, 12), colour).save(path)
    return path


def media(role, segment_id, path):
    return {"id": f"m-{role}-{segment_id}", "role": role, "segment_id": segment_id, "media": {"path": str(path), "type": "image"}}


def film(*segments, media_items=(), fps=24, seed=100, stitch=True):
    return {
        "schema_version": 1,
        "mode": "director",
        "settings": {"fps": fps, "seed": seed, "continuation": {"source": "last_frame", "overlap_frames": 0, "stitch": stitch}},
        "segments": list(segments),
        "media": list(media_items),
    }


def segment(segment_id, frames, sub_type, prompt=None, seed=None):
    return {"id": segment_id, "prompt": prompt or f"prompt {segment_id}", "negative_prompt": "", "frames": frames, "sub_type": sub_type, "seed": seed}


class ClipRunner:
    def __init__(self, tmp_path, *, fail_on=None, error=None, cancel_after=None):
        self.tmp_path = tmp_path
        self.requests = []
        self.start_colours = []
        self.fail_on = fail_on
        self.error = error or CloudRunError("credits", "Out of credits", detail="HTTP 402")
        self.cancel_after = cancel_after
        self.cancelled = False

    def is_cancelled(self):
        return self.cancelled

    def run_blocking(self, request, *, on_progress=None, is_cancelled=None):
        self.requests.append(request)
        number = len(self.requests)
        if self.fail_on == number:
            raise self.error
        if self.cancelled:
            raise CloudRunCancelled()
        starts = request.inputs.get("first_frame") or []
        start = image_colour(starts[0]) if starts else (10 * number, 200, 40)
        self.start_colours.append(start if starts else None)
        ends = request.inputs.get("last_frame") or []
        end = image_colour(ends[0]) if ends else (40, 10 * number, 200)
        if on_progress is not None:
            on_progress(CloudRunProgress(state="running", fraction=0.5, elapsed_s=3))
        path = self.tmp_path / f"shot-{number}.mp4"
        path.write_bytes(fake_video_bytes(float(request.params.get("duration_s") or 1), start=start, end=end))
        if self.cancel_after == number:
            self.cancelled = True
        return CloudRunOutcome(
            artifacts=(CloudRunArtifact(modality="video", index=0, path=path, media_type="video/mp4"),),
            cost=CloudRunCost(amount_usd=Decimal("0.30"), source="provider"),
            seed_used=request.seed,
        )


def saved_shots(emitted):
    return [video.video_path for item in emitted if isinstance(item, GalleryGenerationOutput) for video in item.videos]


def shot_updates(emitted):
    return [(item.shot_id, item.status) for item in emitted if isinstance(item, DirectorShotGenerationOutput)]


def direct(runner, document, params=None):
    pipe = CloudGeneratePipe({
        **CloudGeneratePipe.get_default_config(),
        "model": "fake~director-1",
        "task": "txt2video",
        "params": {"aspect_ratio": "16:9", "duration_s": 99, **(params or {})},
        "director": document,
        "cloud": {"user_ref": "u-1"},
    })
    emitted = []
    result = pipe.process(PipeInput(input={"CLOUD": runner}), emitted.append, is_cancelled=runner.is_cancelled)
    return result.output, emitted


def test_every_shot_is_one_request_in_order_with_its_own_length_seed_and_prompt(tmp_path):
    runner = ClipRunner(tmp_path)
    document = film(segment("a", 48, "t2v"), segment("b", 96, "t2v"), segment("c", 48, "t2v", seed=7))

    direct(runner, document)

    assert [(r.prompt, r.params["duration_s"], r.seed, r.count, r.task) for r in runner.requests] == [
        ("prompt a", 2, 100, 1, "txt2video"),
        ("prompt b", 4, 101, 1, "txt2video"),
        ("prompt c", 2, 7, 1, "txt2video"),
    ]
    assert all(r.params["aspect_ratio"] == "16:9" and r.user_ref == "u-1" for r in runner.requests)


def test_a_continuing_shot_starts_from_the_previous_shots_last_frame(tmp_path):
    runner = ClipRunner(tmp_path)
    start = picture(tmp_path / "start.png", RED)
    end = picture(tmp_path / "end.png", BLUE)
    document = film(
        segment("a", 48, "flf"), segment("b", 48, "chain"),
        media_items=[media("first", "a", start), media("last", "a", end)],
    )

    _, emitted = direct(runner, document)

    first, second = runner.requests
    assert first.task == "img2video" and first.inputs["first_frame"] == [start] and first.inputs["last_frame"] == [end]
    assert second.task == "img2video" and list(second.inputs) == ["first_frame"]
    handed = runner.start_colours[1]
    previous_last = extract_frame(saved_shots(emitted)[0], -1).getpixel((0, 0))
    assert all(abs(a - b) <= 12 for a, b in zip(handed, previous_last))
    assert all(abs(a - b) <= 40 for a, b in zip(handed, BLUE))


def test_a_hard_cut_sends_no_start_frame(tmp_path):
    runner = ClipRunner(tmp_path)

    direct(runner, film(segment("a", 48, "t2v"), segment("b", 48, "t2v")))

    assert [r.inputs for r in runner.requests] == [{}, {}]


def test_each_shot_is_saved_when_it_finishes_and_the_joined_film_comes_out_last(tmp_path):
    runner = ClipRunner(tmp_path)

    output, emitted = direct(runner, film(segment("a", 48, "t2v"), segment("b", 96, "chain")))

    shots = [str(tmp_path / "shot-1.mp4"), str(tmp_path / "shot-2.mp4")]
    assert saved_shots(emitted) == shots
    galleries = [item for item in emitted if isinstance(item, GalleryGenerationOutput)]
    assert len(galleries) == 2 and all(video.temporary is False for item in galleries for video in item.videos)
    (joined,) = output["video"]
    import cv2

    capture = cv2.VideoCapture(joined)
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    assert frames == 16 + 32
    assert output["seed"] == [100]


def test_without_stitching_the_shots_are_saved_and_no_film_is_made(tmp_path):
    output, emitted = direct(ClipRunner(tmp_path), film(segment("a", 48, "t2v"), segment("b", 48, "t2v"), stitch=False))

    assert output["video"] == [] and len(saved_shots(emitted)) == 2


def test_a_successful_film_reports_every_shot_queued_then_generating_then_done(tmp_path):
    _, emitted = direct(ClipRunner(tmp_path), film(segment("a", 48, "t2v"), segment("b", 48, "chain")))

    updates = [item for item in emitted if isinstance(item, DirectorShotGenerationOutput)]
    assert [(u.shot_id, u.status) for u in updates] == [
        ("a", "queued"), ("b", "queued"),
        ("a", "generating"), ("a", "generating"), ("a", "done"),
        ("b", "generating"), ("b", "generating"), ("b", "done"),
    ]
    assert [u.progress for u in updates if u.status == "generating"] == [None, 0.5, None, 0.5]
    done = [u for u in updates if u.status == "done"]
    assert [u.video.video_path for u in done] == saved_shots(emitted)
    assert all((u.shot_index, u.shot_count) == (index, 2) for index, u in enumerate(done))
    galleries = [item for item in emitted if isinstance(item, (GalleryGenerationOutput, DirectorShotGenerationOutput))]
    assert [type(item).__name__ for item in galleries[-2:]] == ["GalleryGenerationOutput", "DirectorShotGenerationOutput"]


def test_progress_names_the_shot_and_carries_its_segment(tmp_path):
    _, emitted = direct(ClipRunner(tmp_path), film(segment("a", 48, "t2v"), segment("b", 48, "chain")))

    progress = [item for item in emitted if isinstance(item, ProgressGenerationOutput) and item.segment_id]
    assert progress[0].state == "Shot 1 of 2: starting" and progress[0].segment_id == "a"
    running = [item for item in progress if item.state.startswith("Shot 2 of 2: Generating")]
    assert running and running[0].segment_id == "b" and running[0].progress.current == 75
    assert any(item.state == "Shot 2 of 2: starting from the previous shot's last frame" for item in progress)


def test_every_shot_records_its_own_cost_line(tmp_path):
    _, emitted = direct(ClipRunner(tmp_path), film(segment("a", 48, "t2v"), segment("b", 96, "chain")))

    costs = [item for item in emitted if isinstance(item, CostGenerationOutput)]
    assert [(c.amount_usd, c.task, c.count, c.params["duration_s"]) for c in costs] == [
        (Decimal("0.30"), "txt2video", 1, 2),
        (Decimal("0.30"), "img2video", 1, 4),
    ]


def test_a_failed_shot_keeps_the_finished_ones_and_says_which_shot_failed(tmp_path):
    runner = ClipRunner(tmp_path, fail_on=3)
    document = film(segment("a", 48, "t2v"), segment("b", 48, "chain"), segment("c", 48, "chain"), segment("d", 48, "t2v"))

    emitted = []
    pipe = CloudGeneratePipe({**CloudGeneratePipe.get_default_config(), "model": "m", "director": document})
    with pytest.raises(CloudRunError) as raised:
        pipe.process(PipeInput(input={"CLOUD": runner}), emitted.append, is_cancelled=runner.is_cancelled)

    error = raised.value
    assert error.kind == "credits" and error.detail == "HTTP 402"
    assert error.context == "Shot 3 of 4 failed. Shots 1-2 were kept."
    assert len(runner.requests) == 3
    assert saved_shots(emitted) == [str(tmp_path / "shot-1.mp4"), str(tmp_path / "shot-2.mp4")]
    assert shot_updates(emitted)[-3:] == [("c", "generating"), ("c", "failed"), ("d", "skipped")]
    (failed,) = [item for item in emitted if isinstance(item, DirectorShotGenerationOutput) and item.status == "failed"]
    assert failed.message == "Out of credits" and failed.video is None


def test_the_first_shot_failing_keeps_nothing(tmp_path):
    runner = ClipRunner(tmp_path, fail_on=1)
    emitted = []
    pipe = CloudGeneratePipe({**CloudGeneratePipe.get_default_config(), "model": "m", "director": film(segment("a", 48, "t2v"), segment("b", 48, "t2v"))})

    with pytest.raises(CloudRunError) as raised:
        pipe.process(PipeInput(input={"CLOUD": runner}), emitted.append)

    assert raised.value.context == "Shot 1 of 2 failed. No shots were finished."
    assert not [item for item in emitted if isinstance(item, GalleryGenerationOutput)]


def test_cancelling_stops_further_paid_calls_and_keeps_finished_shots(tmp_path):
    runner = ClipRunner(tmp_path, cancel_after=1)

    output, emitted = direct(runner, film(segment("a", 48, "t2v"), segment("b", 48, "chain"), segment("c", 48, "t2v")))

    assert len(runner.requests) == 1
    assert output == {"image": [], "video": [], "audio": [], "seed": []}
    assert saved_shots(emitted) == [str(tmp_path / "shot-1.mp4")]
    assert shot_updates(emitted)[-3:] == [("a", "done"), ("b", "skipped"), ("c", "skipped")]


def test_a_cancel_inside_a_shot_marks_it_cancelled_and_skips_the_rest(tmp_path):
    class CancelOnSecond(ClipRunner):
        def run_blocking(self, request, **kwargs):
            if len(self.requests) == 1:
                self.requests.append(request)
                raise CloudRunCancelled()
            return super().run_blocking(request, **kwargs)

    runner = CancelOnSecond(tmp_path)
    output, emitted = direct(runner, film(segment("a", 48, "t2v"), segment("b", 48, "t2v"), segment("c", 48, "t2v")))

    assert len(runner.requests) == 2 and output["video"] == []
    assert len(saved_shots(emitted)) == 1
    assert shot_updates(emitted)[-3:] == [("b", "generating"), ("b", "cancelled"), ("c", "skipped")]


def test_a_single_shot_maps_its_edge_wells_to_start_and_end_frames(tmp_path):
    runner = ClipRunner(tmp_path)
    start = picture(tmp_path / "start.png", RED)
    end = picture(tmp_path / "end.png", BLUE)
    document = {
        "schema_version": 1,
        "mode": "flf",
        "settings": {"fps": 24, "duration": 4, "seed": 5},
        "segments": [{"id": "seg-0", "prompt": "a door opens", "negative_prompt": "blur"}],
        "media": [media("first", "seg-0", start), media("last", "seg-0", end)],
    }

    output, emitted = direct(runner, document)

    (request,) = runner.requests
    assert (request.task, request.prompt, request.negative_prompt, request.seed, request.params["duration_s"]) == (
        "img2video", "a door opens", "blur", 5, 4,
    )
    assert request.inputs == {"first_frame": [start], "last_frame": [end]}
    assert output["video"] == [str(tmp_path / "shot-1.mp4")]
    assert not [item for item in emitted if isinstance(item, ProgressGenerationOutput) and item.state.startswith("Shot")]
    assert not [item for item in emitted if isinstance(item, (DirectorShotGenerationOutput, GalleryGenerationOutput))]


def test_a_single_text_shot_sends_no_frames_and_a_failure_is_not_relabelled(tmp_path):
    runner = ClipRunner(tmp_path, fail_on=1)
    document = {
        "schema_version": 1, "mode": "t2v", "settings": {"fps": 24, "duration": 6, "seed": -1},
        "segments": [{"id": "seg-0", "prompt": "rain"}], "media": [],
    }

    with pytest.raises(CloudRunError) as raised:
        direct(runner, document)

    (request,) = runner.requests
    assert request.inputs == {} and request.seed is None and request.task == "txt2video"
    assert raised.value.context == ""


def test_a_joining_failure_keeps_every_shot_and_says_so(tmp_path, monkeypatch):
    from src.pipelines.pipes.cloud_generate.stitch import StitchError

    def broken(clips, out_path, **kwargs):
        raise StitchError("no frames")

    monkeypatch.setattr(pipe_module, "stitch_clips", broken)

    output, emitted = direct(ClipRunner(tmp_path), film(segment("a", 48, "t2v"), segment("b", 48, "t2v")))

    assert output["video"] == [] and len(saved_shots(emitted)) == 2
    assert any("could not be joined" in item.state for item in emitted if isinstance(item, ProgressGenerationOutput))


def test_the_plan_reads_lengths_from_frames_and_the_documents_fps():
    document = film(segment("a", 120, "t2v"), segment("b", 60, "chain"), fps=30)

    shots = plan_shots(document)

    assert [(shot.duration_s, shot.continues, shot.task, shot.label) for shot in shots] == [
        (4, False, "txt2video", "Shot 1 of 2"),
        (2, True, "img2video", "Shot 2 of 2"),
    ]


def test_a_later_shot_with_its_own_start_picture_is_a_cut_not_a_continuation(tmp_path):
    start = picture(tmp_path / "start.png", RED)
    document = film(segment("a", 48, "t2v"), segment("b", 48, "i2v"), media_items=[media("first", "b", start)])

    shots = plan_shots(document)

    assert (shots[1].continues, shots[1].start_image, shots[1].task) == (False, start, "img2video")
