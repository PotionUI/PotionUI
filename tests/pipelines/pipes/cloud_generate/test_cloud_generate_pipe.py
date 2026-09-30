from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.pipelines.cloud import (
    CloudRunArtifact,
    CloudRunCancelled,
    CloudRunCost,
    CloudRunError,
    CloudRunOutcome,
    CloudRunProgress,
)
from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import (
    CostGenerationOutput,
    GenerationExecutionError,
    ParamGenerationOutput,
    ProgressGenerationOutput,
)
from src.pipelines.pipes.cloud_generate.main import CloudGeneratePipe


class FakeRunner:
    def __init__(self, tmp_path, outcomes=None, error=None, progress=()):
        self.tmp_path = tmp_path
        self.requests = []
        self.staged = []
        self.error = error
        self.progress = progress
        self.outcomes = outcomes

    def run_blocking(self, request, *, on_progress=None, is_cancelled=None):
        self.requests.append(request)
        self.staged.append({role: [(path, path.exists()) for path in paths] for role, paths in request.inputs.items()})
        for item in self.progress:
            on_progress(item)
        if self.error is not None:
            raise self.error
        if self.outcomes is not None:
            return self.outcomes.pop(0)
        artifacts = []
        for index in range(request.count):
            path = self.tmp_path / f"out-{len(self.requests)}-{index}.png"
            Image.new("RGB", (4, 4), (index * 20, 0, 0)).save(path)
            artifacts.append(CloudRunArtifact(modality="image", index=index, path=path, media_type="image/png"))
        return CloudRunOutcome(artifacts=tuple(artifacts), seed_used=request.seed)


def run(runner, config=None, inputs=None):
    pipe = CloudGeneratePipe({**CloudGeneratePipe.get_default_config(), "model": "fake~image-1", **(config or {})})
    emitted = []
    result = pipe.process(PipeInput(input={"CLOUD": runner, **(inputs or {})}), emitted.append)
    return result.output, emitted


def test_one_prompt_for_a_batch_makes_one_request_for_the_whole_count(tmp_path):
    runner = FakeRunner(tmp_path)

    output, _ = run(runner, {"prompts": ["a cat"], "quantity": 3, "params": {"aspect_ratio": "16:9"}}, {"seed": [10, 11, 12]})

    (request,) = runner.requests
    assert (request.prompt, request.count, request.seed) == ("a cat", 3, 10)
    assert request.params == {"aspect_ratio": "16:9"} and request.model == "fake~image-1"
    assert len(output["image"]) == 3 and output["seed"] == [10, 11, 12]


def test_different_prompts_make_one_request_per_image(tmp_path):
    runner = FakeRunner(tmp_path)

    output, _ = run(
        runner,
        {"prompts": [{"positive": "a", "negative": "x"}, {"positive": "b", "negative": "x"}], "quantity": 2},
        {"seed": [5, 6]},
    )

    assert [(r.prompt, r.count, r.seed, r.negative_prompt) for r in runner.requests] == [("a", 1, 5, "x"), ("b", 1, 6, "x")]
    assert len(output["image"]) == 2


def test_a_random_seed_is_not_sent(tmp_path):
    runner = FakeRunner(tmp_path)

    output, _ = run(runner, {"prompts": ["a"]}, {"seed": [-1]})

    assert runner.requests[0].seed is None and output["seed"] == []


def test_input_images_are_staged_under_their_roles_and_removed_afterwards(tmp_path):
    runner = FakeRunner(tmp_path)
    source = Image.new("RGB", (8, 8))

    run(runner, {"prompts": ["edit"], "task": "img_edit", "roles": {"images": "source_image"}}, {"images": [source]})

    (role, staged), = runner.staged[0].items()
    assert role == "source_image" and staged[0][1] is True
    assert not staged[0][0].exists()


def test_images_default_to_the_reference_role(tmp_path):
    runner = FakeRunner(tmp_path)

    run(runner, {"prompts": ["edit"]}, {"images": [Image.new("RGB", (8, 8))]})

    assert list(runner.requests[0].inputs) == ["reference"]


def test_generated_images_are_loaded_and_their_files_removed(tmp_path):
    runner = FakeRunner(tmp_path)

    output, _ = run(runner, {"prompts": ["a"], "quantity": 2})

    assert all(isinstance(image, Image.Image) and image.size == (4, 4) for image in output["image"])
    assert list(tmp_path.glob("out-*")) == []


def test_videos_and_audio_are_returned_as_paths(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    audio = tmp_path / "sound.mp3"
    audio.write_bytes(b"a")
    outcomes = [CloudRunOutcome(artifacts=(
        CloudRunArtifact(modality="video", index=0, path=video),
        CloudRunArtifact(modality="audio", index=1, path=audio),
    ))]

    output, _ = run(FakeRunner(tmp_path, outcomes=outcomes), {"prompts": ["a"], "task": "txt2video"})

    assert output["video"] == [str(video)] and output["audio"] == [str(audio)] and output["image"] == []


def test_the_model_is_recorded_for_every_output(tmp_path):
    _, emitted = run(FakeRunner(tmp_path), {"prompts": ["a"], "quantity": 2})

    (param,) = [item for item in emitted if isinstance(item, ParamGenerationOutput)]
    assert param.name == "model" and param.values == ["fake~image-1", "fake~image-1"]


def test_provider_progress_is_reported_as_generation_progress(tmp_path):
    progress = (
        CloudRunProgress(state="queued", queue_position=2),
        CloudRunProgress(state="running", fraction=0.5, elapsed_s=12),
        CloudRunProgress(state="fetching"),
    )

    _, emitted = run(FakeRunner(tmp_path, progress=progress), {"prompts": ["a"]})

    shown = [item for item in emitted if isinstance(item, ProgressGenerationOutput)]
    assert "position 2" in shown[0].state
    assert shown[1].progress.current == 50 and shown[1].progress.max == 100
    assert shown[2].state == "Downloading the result"


def test_a_cancelled_run_returns_nothing(tmp_path):
    output, _ = run(FakeRunner(tmp_path, error=CloudRunCancelled()), {"prompts": ["a"]})

    assert output == {"image": [], "video": [], "audio": [], "seed": []}


def test_a_provider_failure_propagates_with_its_kind(tmp_path):
    runner = FakeRunner(tmp_path, error=CloudRunError("credits", "Out of credits"))

    with pytest.raises(CloudRunError) as raised:
        run(runner, {"prompts": ["a"]})

    assert raised.value.kind == "credits"


def test_without_a_cloud_service_the_pipe_refuses_to_run():
    pipe = CloudGeneratePipe({**CloudGeneratePipe.get_default_config(), "model": "m"})

    with pytest.raises(GenerationExecutionError):
        pipe.process(PipeInput(input={}), lambda _: None)


def test_without_a_model_the_pipe_refuses_to_run(tmp_path):
    pipe = CloudGeneratePipe(CloudGeneratePipe.get_default_config())

    with pytest.raises(GenerationExecutionError):
        pipe.process(PipeInput(input={"CLOUD": FakeRunner(tmp_path)}), lambda _: None)


def test_files_fetched_for_earlier_requests_are_removed_when_a_later_one_fails(tmp_path):
    first = tmp_path / "first.mp4"
    first.write_bytes(b"v")
    outcomes = [CloudRunOutcome(artifacts=(CloudRunArtifact(modality="video", index=0, path=first),))]

    class Flaky(FakeRunner):
        def run_blocking(self, request, *, on_progress=None, is_cancelled=None):
            if self.outcomes:
                return self.outcomes.pop(0)
            raise CloudRunError("credits", "Out of credits")

    with pytest.raises(CloudRunError):
        run(Flaky(tmp_path, outcomes=outcomes), {"prompts": ["a", "b"], "quantity": 2, "task": "txt2video"})

    assert not first.exists()


def test_files_fetched_for_earlier_requests_are_removed_when_a_later_one_is_cancelled(tmp_path):
    first = tmp_path / "first.mp4"
    first.write_bytes(b"v")
    outcomes = [CloudRunOutcome(artifacts=(CloudRunArtifact(modality="video", index=0, path=first),))]

    class Cancelling(FakeRunner):
        def run_blocking(self, request, *, on_progress=None, is_cancelled=None):
            if self.outcomes:
                return self.outcomes.pop(0)
            raise CloudRunCancelled()

    output, _ = run(Cancelling(tmp_path, outcomes=outcomes), {"prompts": ["a", "b"], "quantity": 2, "task": "txt2video"})

    assert output["video"] == [] and not first.exists()


def test_explicit_params_win_over_provider_options(tmp_path):
    runner = FakeRunner(tmp_path)

    run(
        runner,
        {"prompts": ["a"], "params": {"aspect_ratio": "16:9"}, "options": {"aspect_ratio": "1:1", "x.style": "noir"}},
    )

    assert dict(runner.requests[0].params) == {"aspect_ratio": "16:9", "x.style": "noir"}


def test_empty_and_missing_values_are_not_sent(tmp_path):
    runner = FakeRunner(tmp_path)

    run(
        runner,
        {
            "prompts": ["a"],
            "params": {"resolution": None, "quality": "", "background": False, "duration_s": 0},
            "options": {"x.other": None, "x.blank": ""},
        },
    )

    assert dict(runner.requests[0].params) == {"background": False, "duration_s": 0}


def test_options_that_are_not_an_object_are_ignored(tmp_path):
    runner = FakeRunner(tmp_path)

    run(runner, {"prompts": ["a"], "params": {"quality": 3}, "options": "None"})

    assert dict(runner.requests[0].params) == {"quality": 3}


def costs_of(emitted):
    return [item for item in emitted if isinstance(item, CostGenerationOutput)]


def costed_outcome(tmp_path, name, amount, source="provider"):
    path = tmp_path / name
    Image.new("RGB", (4, 4)).save(path)
    return CloudRunOutcome(
        artifacts=(CloudRunArtifact(modality="image", index=0, path=path, media_type="image/png"),),
        cost=CloudRunCost(amount_usd=Decimal(amount), source=source),
    )


def test_a_successful_request_emits_its_cost_with_what_an_estimate_needs(tmp_path):
    runner = FakeRunner(tmp_path, outcomes=[costed_outcome(tmp_path, "a.png", "0.0731")])

    _, emitted = run(runner, {"prompts": ["a"], "task": "txt2img", "params": {"aspect_ratio": "16:9"}})

    (cost,) = costs_of(emitted)
    assert cost.model == "fake~image-1" and cost.amount_usd == Decimal("0.0731") and cost.source == "provider"
    assert (cost.task, cost.count, cost.outputs, cost.params) == ("txt2img", 1, 1, {"aspect_ratio": "16:9"})


def test_a_request_the_provider_did_not_price_emits_a_cost_without_an_amount(tmp_path):
    cost = costs_of(run(FakeRunner(tmp_path), {"prompts": ["a"], "quantity": 2})[1])[0]

    assert cost.amount_usd is None and cost.source == "provider" and cost.count == 2 and cost.outputs == 2


def test_the_source_the_provider_gave_is_passed_on(tmp_path):
    runner = FakeRunner(tmp_path, outcomes=[costed_outcome(tmp_path, "a.png", "0.02", source="estimate")])

    (cost,) = costs_of(run(runner, {"prompts": ["a"]})[1])

    assert cost.source == "estimate"


def test_each_request_emits_its_own_cost(tmp_path):
    runner = FakeRunner(tmp_path, outcomes=[costed_outcome(tmp_path, "a.png", "0.01"), costed_outcome(tmp_path, "b.png", "0.02")])

    _, emitted = run(runner, {"prompts": ["a", "b"], "quantity": 2})

    assert [cost.amount_usd for cost in costs_of(emitted)] == [Decimal("0.01"), Decimal("0.02")]


def test_a_cancelled_run_emits_no_cost(tmp_path):
    _, emitted = run(FakeRunner(tmp_path, error=CloudRunCancelled()), {"prompts": ["a"]})

    assert costs_of(emitted) == []


def test_a_failed_run_emits_no_cost(tmp_path):
    emitted = []
    pipe = CloudGeneratePipe({**CloudGeneratePipe.get_default_config(), "model": "fake~image-1", "prompts": ["a"]})

    with pytest.raises(CloudRunError):
        pipe.process(
            PipeInput(input={"CLOUD": FakeRunner(tmp_path, error=CloudRunError("credits", "Out of credits"))}),
            emitted.append,
        )

    assert costs_of(emitted) == []


def test_a_request_billed_before_a_later_one_was_cancelled_still_reports_its_cost(tmp_path):
    outcomes = [costed_outcome(tmp_path, "a.png", "0.03")]

    class Cancelling(FakeRunner):
        def run_blocking(self, request, *, on_progress=None, is_cancelled=None):
            if self.outcomes:
                return self.outcomes.pop(0)
            raise CloudRunCancelled()

    _, emitted = run(Cancelling(tmp_path, outcomes=outcomes), {"prompts": ["a", "b"], "quantity": 2})

    assert [cost.amount_usd for cost in costs_of(emitted)] == [Decimal("0.03")]
