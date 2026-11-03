import pytest

from src.features.remote_execution.policy import should_requeue
from src.features.remote_execution.worker.executor import PipeExecutionError, WorkerPipelineExecutor
from src.pipelines.contracts import PipeOutput
from src.platform.runtime import cuda_allocator
from src.platform.runtime.cuda_allocator import WORKER_RESTART_HINT
from src.platform.worker_protocol import JobErrorV1, ProcessedPipelineV1, ProcessedPipeV1

DEVICE_NOT_READY = "CUDA driver error: device not ready"


class FakeCatalog:
    def __init__(self, *classes):
        self._classes = {cls.name: cls for cls in classes}

    def get_pipe(self, name):
        return self._classes.get(name)


class FakePipe:
    name = "fake/pipe"
    description = "fake"
    built = 0

    def __init__(self, config):
        type(self).built += 1
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
        return PipeOutput(output={})


@pytest.fixture
def pipe():
    return type("CountingPipe", (FakePipe,), {"built": 0})


@pytest.fixture
def damaged(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", DEVICE_NOT_READY)


def _pipeline(*configs, **fields):
    return ProcessedPipelineV1(pipes=tuple(
        ProcessedPipeV1(pipe_id=f"p{index}", pipe_type=FakePipe.name, config=config, inputs={}, **fields)
        for index, config in enumerate(configs)
    ))


def _run(tmp_path, pipe_class, pipeline, device="cuda", emit=lambda event: None):
    executor = WorkerPipelineExecutor(
        FakeCatalog(pipe_class), device=device, dtype="fp16", vram_limit_gb=None, artifacts_dir=tmp_path,
    )
    executor.run(pipeline, emit=emit, is_cancelled=lambda: False)


def test_a_damaged_allocator_refuses_a_cuda_pipe_before_it_is_built(tmp_path, pipe, damaged):
    events = []

    with pytest.raises(PipeExecutionError) as refusal:
        _run(tmp_path, pipe, _pipeline({}), emit=events.append)

    assert refusal.value.code == "worker_gpu_restart_required"
    assert refusal.value.retryable is False
    assert refusal.value.pipe_id == "p0"
    assert DEVICE_NOT_READY in refusal.value.message
    assert refusal.value.message.endswith(WORKER_RESTART_HINT)
    assert pipe.built == 0
    assert [event.kind for event in events] == ["pipe_started"]


@pytest.mark.parametrize("blank", [None, ""])
def test_a_damaged_allocator_refuses_a_pipe_whose_device_is_blank(tmp_path, pipe, damaged, blank):
    with pytest.raises(PipeExecutionError):
        _run(tmp_path, pipe, _pipeline({"device": blank}), device="cpu")

    assert pipe.built == 0


def test_a_damaged_allocator_refuses_a_pipe_pinned_to_cuda_on_a_cpu_worker(tmp_path, pipe, damaged):
    with pytest.raises(PipeExecutionError):
        _run(tmp_path, pipe, _pipeline({"device": "cuda:1"}), device="cpu")

    assert pipe.built == 0


def test_a_damaged_allocator_does_not_stop_a_cpu_worker(tmp_path, pipe, damaged):
    _run(tmp_path, pipe, _pipeline({}), device="cpu")

    assert pipe.built == 1


def test_a_damaged_allocator_does_not_stop_a_pipe_pinned_to_cpu(tmp_path, pipe, damaged):
    _run(tmp_path, pipe, _pipeline({"device": "cpu"}))

    assert pipe.built == 1


def test_a_damaged_allocator_ignores_a_disabled_pipe(tmp_path, pipe, damaged):
    _run(tmp_path, pipe, _pipeline({}, enabled=False))

    assert pipe.built == 0


def test_a_refused_pipe_stops_the_pipeline_at_that_pipe(tmp_path, pipe, damaged):
    with pytest.raises(PipeExecutionError) as refusal:
        _run(tmp_path, pipe, _pipeline({"device": "cpu"}, {}, {"device": "cpu"}))

    assert refusal.value.pipe_id == "p1"
    assert pipe.built == 1


def test_a_refusal_is_never_requeued(tmp_path, pipe, damaged):
    with pytest.raises(PipeExecutionError) as refusal:
        _run(tmp_path, pipe, _pipeline({}))

    error = JobErrorV1(code=refusal.value.code, message=refusal.value.message, retryable=refusal.value.retryable)
    assert should_requeue(error, attempt=1, max_attempts=3) is False


def test_an_intact_allocator_refuses_nothing(tmp_path, pipe, monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)

    _run(tmp_path, pipe, _pipeline({}))

    assert pipe.built == 1
