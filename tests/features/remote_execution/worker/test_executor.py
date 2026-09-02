"""WorkerPipelineExecutor against fake pipes only - no torch, no model load."""

from pathlib import Path

import pytest
from PIL import Image

from src.features.remote_execution.output_codec import decode_output
from src.features.remote_execution.worker.executor import (
    PipeExecutionError,
    WorkerPipelineExecutor,
)
from src.pipelines.contracts import (
    IOType,
    PipeConfigSpec,
    PipeInputSpec,
    PipeOutputSpec,
    PipeOutput,
)
from src.pipelines.outputs import (
    GalleryGenerationOutput,
    Icon,
    ImageGenerationOutput,
    ProgressGenerationOutput,
    Progress,
    VideoGenerationOutput,
)
from src.platform.worker_protocol import ProcessedPipelineV1, ProcessedPipeV1


def _decode(event, *, tmp_path: Path, pipe_index=0, pipe_name=None):
    """The same reconstruction `RemoteNativeBackend._handle_event` performs:
    the artifacts an event carries are already local files under
    `tmp_path` (that's where the executor wrote them), so this just builds
    the `{artifact_id: path}` map decode_output needs."""
    artifact_paths = {a.artifact_id: tmp_path / a.filename for a in event.artifacts}
    return decode_output(event.payload["output"], artifact_paths, pipe_index=pipe_index, pipe_name=pipe_name)


class FakeCatalog:
    def __init__(self, classes):
        self._classes = classes

    def get_pipe(self, name):
        return self._classes.get(name)


class LoaderPipe:
    name = "loader/fake"
    description = "fake"
    execution_count = 0

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {"path": "models/checkpoints/default.safetensors"}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="model", io_type=IOType.MODEL)]

    @classmethod
    def configuration(cls):
        return [PipeConfigSpec(name="path", param_type=str, default="x")]

    def process(self, pipe_input, generation_outputs):
        type(self).execution_count += 1
        generation_outputs(ProgressGenerationOutput(state="loading"))
        return PipeOutput(output={"model": "a-loaded-model"})


class GeneratorPipe:
    name = "generator/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {"steps": 20}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="model", io_type=IOType.MODEL, required=True)]

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="image", io_type=IOType.IMAGE)]

    @classmethod
    def configuration(cls):
        return [
            PipeConfigSpec(name="steps", param_type=int, default=20),
            PipeConfigSpec(name="device", param_type=str, default="cuda"),
        ]

    def process(self, pipe_input, generation_outputs, is_cancelled=None):
        assert pipe_input.input["model"] == "a-loaded-model"
        assert self.config["device"] == "cuda:7"  # worker-injected, not the class default
        generation_outputs(ProgressGenerationOutput(
            state="denoising", progress=Progress(current=1, max=2),
        ))
        image = Image.new("RGB", (4, 4))
        generation_outputs(ImageGenerationOutput(image=image, temporary=False))
        return PipeOutput(output={"image": image})


class FailingPipe:
    name = "failing/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        raise RuntimeError("boom")


class ModelsAwarePipe:
    """Declares the MODELS SERVICE input and records exactly what it received,
    so the test can tell "the real object" from "silently injected None"."""

    name = "models_aware/fake"
    description = "fake"
    received_models = "not set"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="models", io_type=IOType.SERVICE, required=False)]

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        type(self).received_models = pipe_input.input.get("models")
        return PipeOutput(output={})


class CondEncoderPipe:
    name = "cond_encoder/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return [PipeOutputSpec(name="conditioning", io_type=IOType.CONDITIONING, is_array=True)]

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        return PipeOutput(output={"conditioning": ["cond-0", "cond-1"]})


class SingleCondConsumerPipe:
    """Declares a NON-array conditioning input; records what actually arrived."""

    name = "cond_single/fake"
    description = "fake"
    received = "not set"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="conditioning", io_type=IOType.CONDITIONING, required=True, is_array=False)]

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        type(self).received = pipe_input.input["conditioning"]
        return PipeOutput(output={})


class ArrayCondConsumerPipe:
    name = "cond_array/fake"
    description = "fake"
    received = "not set"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return [PipeInputSpec(name="conditioning", io_type=IOType.CONDITIONING, required=True, is_array=True)]

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        type(self).received = pipe_input.input["conditioning"]
        return PipeOutput(output={})


class GalleryEmittingPipe:
    """Mirrors what the local `gallery` pipe (and generator base's
    `emit_gallery`) actually emit at the end of a real pipeline: one
    GalleryGenerationOutput wrapping the final images."""

    name = "gallery_emit/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        images = [
            ImageGenerationOutput(image=Image.new("RGB", (4, 4)), temporary=False, seed=111, derived=False),
            ImageGenerationOutput(image=Image.new("RGB", (4, 4)), temporary=False, seed=222, derived=True),
        ]
        generation_outputs(GalleryGenerationOutput(images=images))
        return PipeOutput(output={})


class TemporaryImagePipe:
    name = "temp_image/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        big = Image.new("RGB", (2000, 1000))
        generation_outputs(ImageGenerationOutput(image=big, temporary=True, seed=7))
        return PipeOutput(output={})


class IconProgressPipe:
    """Emits a Progress output carrying an Icon and a title - the exact
    fields the old per-kind `pipe_progress` mapping used to lose."""

    name = "icon_progress/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        generation_outputs(ProgressGenerationOutput(
            state="denoising", icon=Icon(name="bolt", effect="pulse"), title="Denoising",
            progress=Progress(current=3, max=10),
        ))
        return PipeOutput(output={})


class MissingVideoFilePipe:
    """Emits a VideoGenerationOutput whose file was never written - the
    OutputEncodeError path (a media Path field that doesn't exist on disk)."""

    name = "missing_video/fake"
    description = "fake"

    def __init__(self, config):
        self.config = config

    @classmethod
    def get_default_config(cls):
        return {}

    @classmethod
    def inputs(cls):
        return []

    @classmethod
    def outputs(cls):
        return []

    @classmethod
    def configuration(cls):
        return []

    def process(self, pipe_input, generation_outputs):
        generation_outputs(VideoGenerationOutput(video_path=Path("/does/not/exist.mp4"), temporary=False))
        return PipeOutput(output={})


CATALOG = FakeCatalog({
    "loader/fake": LoaderPipe,
    "generator/fake": GeneratorPipe,
    "failing/fake": FailingPipe,
    "models_aware/fake": ModelsAwarePipe,
    "cond_encoder/fake": CondEncoderPipe,
    "cond_single/fake": SingleCondConsumerPipe,
    "cond_array/fake": ArrayCondConsumerPipe,
    "gallery_emit/fake": GalleryEmittingPipe,
    "temp_image/fake": TemporaryImagePipe,
    "icon_progress/fake": IconProgressPipe,
    "missing_video/fake": MissingVideoFilePipe,
})


def _pipeline():
    return ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="loader", pipe_type="loader/fake", config={}, inputs={}),
        ProcessedPipeV1(
            pipe_id="gen", pipe_type="generator/fake", config={"steps": 30},
            inputs={"model": [{"provider": "loader", "output_var": "model", "enabled": True}]},
        ),
    ))


def _executor(tmp_path: Path) -> WorkerPipelineExecutor:
    return WorkerPipelineExecutor(
        CATALOG, device="cuda:7", dtype="bf16", vram_limit_gb=10.0, artifacts_dir=tmp_path,
    )


def test_runs_pipes_in_order_wiring_outputs_between_them(tmp_path):
    events = []
    executor = _executor(tmp_path)

    executor.run(_pipeline(), emit=events.append, is_cancelled=lambda: False)

    kinds = [e.kind for e in events]
    assert kinds == [
        "pipe_started", "pipe_progress",
        "pipe_started", "pipe_progress", "output",
    ]


def test_an_array_output_feeding_a_single_input_delivers_the_first_element(tmp_path):
    """Mirrors the local engine (the remote Anima 'list has no n_embeds' failure):
    prompt_encoder emits conditioning as an array; a non-array input takes [0]."""
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="enc", pipe_type="cond_encoder/fake", config={}, inputs={}),
        ProcessedPipeV1(
            pipe_id="use", pipe_type="cond_single/fake", config={},
            inputs={"conditioning": [{"provider": "enc", "output_var": "conditioning", "enabled": True}]},
        ),
    ))
    SingleCondConsumerPipe.received = "not set"

    _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)

    assert SingleCondConsumerPipe.received == "cond-0"


def test_an_array_output_feeding_an_array_input_passes_verbatim_unwrapped(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="enc", pipe_type="cond_encoder/fake", config={}, inputs={}),
        ProcessedPipeV1(
            pipe_id="use", pipe_type="cond_array/fake", config={},
            inputs={"conditioning": [{"provider": "enc", "output_var": "conditioning", "enabled": True}]},
        ),
    ))
    ArrayCondConsumerPipe.received = "not set"

    _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)

    assert ArrayCondConsumerPipe.received == ["cond-0", "cond-1"]


def test_an_image_output_is_written_to_disk_hashed_and_round_trips(tmp_path):
    events = []
    _executor(tmp_path).run(_pipeline(), emit=events.append, is_cancelled=lambda: False)

    output_events = [e for e in events if e.kind == "output"]
    assert len(output_events) == 1
    artifact = output_events[0].artifacts[0]
    assert artifact.kind == "image"
    written = tmp_path / artifact.filename
    assert written.exists()
    assert written.stat().st_size == artifact.size_bytes

    decoded = _decode(output_events[0], tmp_path=tmp_path, pipe_index=1, pipe_name="generator/fake")
    assert isinstance(decoded, ImageGenerationOutput)
    assert decoded.temporary is False
    assert decoded.pipe_id == 1 and decoded.pipe_name == "generator/fake"
    assert decoded.image.size == (4, 4)


def test_a_gallery_output_arrives_as_one_output_event_that_decodes_to_one_gallery(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="g", pipe_type="gallery_emit/fake", config={}, inputs={}),
    ))
    events = []
    _executor(tmp_path).run(pipeline, emit=events.append, is_cancelled=lambda: False)

    output_events = [e for e in events if e.kind == "output"]
    assert len(output_events) == 1
    assert len(output_events[0].artifacts) == 2

    decoded = _decode(output_events[0], tmp_path=tmp_path)
    assert isinstance(decoded, GalleryGenerationOutput)
    assert sorted(i.seed for i in decoded.images) == [111, 222]
    assert [i.derived for i in sorted(decoded.images, key=lambda i: i.seed)] == [False, True]


def test_a_temporary_image_produces_a_downscaled_preview_that_decodes_as_temporary(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="p", pipe_type="temp_image/fake", config={}, inputs={}),
    ))
    events = []
    _executor(tmp_path).run(pipeline, emit=events.append, is_cancelled=lambda: False)

    output_events = [e for e in events if e.kind == "output"]
    assert len(output_events) == 1
    artifact = output_events[0].artifacts[0]
    assert artifact.media_type == "image/jpeg"

    written = tmp_path / artifact.filename
    with Image.open(written) as saved:
        assert max(saved.size) <= 768

    decoded = _decode(output_events[0], tmp_path=tmp_path)
    assert isinstance(decoded, ImageGenerationOutput)
    assert decoded.temporary is True
    assert decoded.seed == 7


def test_a_progress_outputs_icon_and_title_survive_onto_the_wire(tmp_path):
    """The whole point of a per-output payload instead of the old
    kind/progress/detail-only pipe_progress event: nothing about a
    ProgressGenerationOutput is lost in transit."""
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="p", pipe_type="icon_progress/fake", config={}, inputs={}),
    ))
    events = []
    _executor(tmp_path).run(pipeline, emit=events.append, is_cancelled=lambda: False)

    progress_events = [e for e in events if e.kind == "pipe_progress"]
    assert len(progress_events) == 1
    assert progress_events[0].progress == pytest.approx(0.3)
    assert progress_events[0].detail == "denoising"

    decoded = _decode(progress_events[0], tmp_path=tmp_path)
    assert isinstance(decoded, ProgressGenerationOutput)
    assert decoded.icon.name == "bolt" and decoded.icon.effect == "pulse"
    assert decoded.title == "Denoising"
    assert decoded.progress.current == 3 and decoded.progress.max == 10


def test_a_media_output_whose_file_never_existed_fails_loudly_not_silently(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="p", pipe_type="missing_video/fake", config={}, inputs={}),
    ))
    with pytest.raises(PipeExecutionError) as exc_info:
        _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)
    assert exc_info.value.code == "output_encode_failed"
    assert exc_info.value.retryable is False
    assert exc_info.value.pipe_id == "p"


def test_temporary_images_produce_no_output_event(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="loader", pipe_type="loader/fake", config={}, inputs={}),
    ))
    events = []
    _executor(tmp_path).run(pipeline, emit=events.append, is_cancelled=lambda: False)
    assert not any(e.kind == "output" for e in events)


def test_disabled_pipes_are_skipped_entirely(tmp_path):
    LoaderPipe.execution_count = 0
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="loader", pipe_type="loader/fake", config={}, inputs={}, enabled=False),
    ))
    _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)
    assert LoaderPipe.execution_count == 0


def test_a_raising_pipe_becomes_a_retryable_pipe_execution_error(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="bad", pipe_type="failing/fake", config={}, inputs={}),
    ))
    with pytest.raises(PipeExecutionError) as exc_info:
        _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)
    assert exc_info.value.retryable is True
    assert exc_info.value.pipe_id == "bad"


def test_an_unknown_pipe_type_is_not_retryable(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="x", pipe_type="does/not-exist", config={}, inputs={}),
    ))
    with pytest.raises(PipeExecutionError) as exc_info:
        _executor(tmp_path).run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)
    assert exc_info.value.retryable is False
    assert exc_info.value.code == "unknown_pipe"


def test_the_models_service_is_injected_from_the_constructor_argument(tmp_path):
    sentinel_model_lifecycle = object()
    executor = WorkerPipelineExecutor(
        CATALOG, device="cuda:7", dtype="bf16", vram_limit_gb=10.0, artifacts_dir=tmp_path,
        model_lifecycle=sentinel_model_lifecycle,
    )
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="m", pipe_type="models_aware/fake", config={}, inputs={}),
    ))

    executor.run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)

    assert ModelsAwarePipe.received_models is sentinel_model_lifecycle


def test_the_models_service_is_none_when_the_executor_has_no_lifecycle(tmp_path):
    executor = _executor(tmp_path)  # constructed without model_lifecycle
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="m", pipe_type="models_aware/fake", config={}, inputs={}),
    ))

    executor.run(pipeline, emit=lambda e: None, is_cancelled=lambda: False)

    assert ModelsAwarePipe.received_models is None


def test_cancellation_stops_before_the_next_pipe_starts(tmp_path):
    events = []
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 1  # cancel between the two pipes

    _executor(tmp_path).run(_pipeline(), emit=events.append, is_cancelled=is_cancelled)

    kinds = [e.kind for e in events]
    assert "pipe_started" in kinds
    assert not any(e.kind == "output" for e in events)
