import pytest
import torch

from src.features.remote_execution.policy import should_requeue
from src.features.remote_execution.worker.executor import PipeExecutionError, WorkerPipelineExecutor
from src.pipelines.contracts import PipeOutput
from src.platform.runtime import cuda_allocator
from src.platform.runtime.cuda_allocator import DRIVER_OOM_POLICY_ENV, WORKER_RESTART_HINT, CudaAllocatorDamaged
from src.platform.runtime.native.errors import HostMemoryExhaustedError, SamplingCancelled
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


@pytest.fixture
def intact(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")


def _raising(error):
    def process(self, pipe_input, generation_outputs):
        raise error

    return type("RaisingPipe", (FakePipe,), {"process": process, "built": 0})


def _failure_of(tmp_path, error, **run):
    with pytest.raises(PipeExecutionError) as failure:
        _run(tmp_path, _raising(error), _pipeline({}), **run)
    return failure.value


def _is_requeued(failure):
    error = JobErrorV1(code=failure.code, message=failure.message, retryable=failure.retryable)
    return should_requeue(error, attempt=1, max_attempts=3)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"), "cuda_oom"),
        (HostMemoryExhaustedError("not enough host RAM"), "host_ram_oom"),
        (FileNotFoundError("/models/checkpoints/sdxl.safetensors"), "missing_model_file"),
    ],
)
def test_a_classified_failure_travels_under_its_category_and_stays_retryable(tmp_path, intact, error, code):
    failure = _failure_of(tmp_path, error)

    assert failure.code == code
    assert failure.message == str(error)
    assert _is_requeued(failure) is True
    assert cuda_allocator.allocator_damage() is None


def test_a_failure_nothing_classifies_stays_pipe_failed(tmp_path, intact):
    failure = _failure_of(tmp_path, RuntimeError("boom"))

    assert failure.code == "pipe_failed"
    assert _is_requeued(failure) is True


def test_a_cancelled_sampling_loop_is_not_read_as_an_allocator_failure(tmp_path, intact, monkeypatch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")

    failure = _failure_of(tmp_path, SamplingCancelled(step_index=3))

    assert failure.code == "pipe_failed"
    assert cuda_allocator.allocator_damage() is None


@pytest.mark.parametrize("text", ["CUDA driver error: device not ready", "CUDA driver error: out of memory"])
def test_a_driver_oom_that_damages_the_allocator_asks_for_a_worker_restart_at_once(tmp_path, intact, monkeypatch, text):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")

    failure = _failure_of(tmp_path, RuntimeError(text))

    assert failure.code == "worker_gpu_restart_required"
    assert failure.message == text
    assert _is_requeued(failure) is False
    assert cuda_allocator.allocator_damage() == text


def test_the_pipe_after_a_fatal_driver_oom_is_refused_unbuilt(tmp_path, pipe, intact, monkeypatch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")
    _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY))

    with pytest.raises(PipeExecutionError) as refusal:
        _run(tmp_path, pipe, _pipeline({}))

    assert refusal.value.code == "worker_gpu_restart_required"
    assert pipe.built == 0


def test_only_the_first_driver_oom_turns_the_segments_off(tmp_path, intact, monkeypatch):
    stopped = []
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: stopped.append(True))
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")

    for _ in range(2):
        failure = _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY), device="cpu")
        assert failure.code == "worker_gpu_restart_required"

    assert stopped == [True]


def test_a_driver_oom_that_is_retried_stays_a_retryable_cuda_oom(tmp_path, intact, monkeypatch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "retry")

    failure = _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY))

    assert failure.code == "cuda_oom"
    assert _is_requeued(failure) is True
    assert cuda_allocator.allocator_damage() is None


def test_a_driver_oom_under_wsl2_is_fatal_by_default(tmp_path, intact, monkeypatch):
    monkeypatch.delenv(DRIVER_OOM_POLICY_ENV, raising=False)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release: True)

    assert _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY)).code == "worker_gpu_restart_required"
    assert cuda_allocator.allocator_damage() == DEVICE_NOT_READY


def test_a_driver_oom_under_native_linux_keeps_retrying_by_default(tmp_path, intact, monkeypatch):
    monkeypatch.delenv(DRIVER_OOM_POLICY_ENV, raising=False)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release: False)

    assert _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY)).code == "cuda_oom"
    assert cuda_allocator.allocator_damage() is None


def test_a_driver_oom_with_expandable_segments_off_keeps_retrying_under_wsl2(tmp_path, intact, monkeypatch):
    monkeypatch.delenv(DRIVER_OOM_POLICY_ENV, raising=False)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release: True)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:False")

    assert _failure_of(tmp_path, RuntimeError(DEVICE_NOT_READY)).code == "cuda_oom"
    assert cuda_allocator.allocator_damage() is None


def test_a_later_driver_error_on_a_damaged_allocator_asks_for_a_worker_restart(tmp_path, damaged):
    failure = _failure_of(tmp_path, RuntimeError("CUDA driver error: invalid argument"), device="cpu")

    assert failure.code == "worker_gpu_restart_required"
    assert _is_requeued(failure) is False


def test_the_host_restart_category_never_leaves_a_worker(tmp_path, intact):
    failure = _failure_of(tmp_path, CudaAllocatorDamaged(DEVICE_NOT_READY))

    assert failure.code == "worker_gpu_restart_required"
