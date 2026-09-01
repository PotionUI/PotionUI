"""WorkerPipelineExecutor against fake pipes only - no torch, no model load."""

from pathlib import Path

import pytest
from PIL import Image

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
from src.pipelines.outputs import ImageGenerationOutput, ProgressGenerationOutput, Progress
from src.platform.worker_protocol import ProcessedPipelineV1, ProcessedPipeV1


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


CATALOG = FakeCatalog({
    "loader/fake": LoaderPipe,
    "generator/fake": GeneratorPipe,
    "failing/fake": FailingPipe,
    "models_aware/fake": ModelsAwarePipe,
    "cond_encoder/fake": CondEncoderPipe,
    "cond_single/fake": SingleCondConsumerPipe,
    "cond_array/fake": ArrayCondConsumerPipe,
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
        "pipe_started", "pipe_progress", "artifact",
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


def test_artifact_is_written_to_disk_and_hashed(tmp_path):
    events = []
    _executor(tmp_path).run(_pipeline(), emit=events.append, is_cancelled=lambda: False)

    artifact_events = [e for e in events if e.kind == "artifact"]
    assert len(artifact_events) == 1
    artifact = artifact_events[0].artifacts[0]
    assert artifact.kind == "image"
    written = tmp_path / artifact.filename
    assert written.exists()
    assert written.stat().st_size == artifact.size_bytes


def test_temporary_images_produce_no_artifact(tmp_path):
    pipeline = ProcessedPipelineV1(pipes=(
        ProcessedPipeV1(pipe_id="loader", pipe_type="loader/fake", config={}, inputs={}),
    ))
    events = []
    _executor(tmp_path).run(pipeline, emit=events.append, is_cancelled=lambda: False)
    assert not any(e.kind == "artifact" for e in events)


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
    assert not any(e.kind == "artifact" for e in events)
