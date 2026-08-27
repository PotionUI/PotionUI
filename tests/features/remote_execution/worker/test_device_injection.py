"""Parity between the worker's device/dtype/vram injection and
NativeBackend.prepare_pipes - the local generation path's equivalent step.

Both must produce the same effective (device, dtype, vram_limit_gb) for a
representative pipe when fed the same worker-decided values, because the
precedence ladder (class defaults < backend/worker injection < preset config)
is the same ladder on both sides of the dispatch decision.
"""

from types import SimpleNamespace

from src.features.backends.native_backend import NativeBackend
from src.features.generation.engine import deep_update, validate_pipe_configuration
from src.features.remote_execution.worker.device_injection import inject_worker_device

WORKER_DEVICE = "cuda:0"
WORKER_DTYPE = "bf16"
WORKER_VRAM_GB = 22.5


class PlainPipe:
    """Representative of the overwhelming majority of native pipes: no
    device/dtype/vram_limit_gb declared in its own configuration spec - those
    three arrive only via backend/worker injection."""

    name = "generator/plain"

    @classmethod
    def get_default_config(cls):
        return {"steps": 20, "cfg": 7.0}

    @classmethod
    def configuration(cls):
        return []

    @classmethod
    def validate_config(cls, config):
        return None


def _native_backend():
    config = SimpleNamespace(
        id="native-test", name="native-test", engine="native",
        device=WORKER_DEVICE, dtype=WORKER_DTYPE, gpu_max_vram=WORKER_VRAM_GB,
    )
    return NativeBackend(config, generation_engine=None)


def _local_effective_config(pipe_class, preset_config: dict) -> dict:
    """What a local generation actually runs the pipe with: NativeBackend's
    setdefault injection (native_backend.py), then PipelineExecutor's merge
    (generation.py:601-604)."""
    pipes = [{"config": dict(preset_config)}]
    injected = _native_backend().prepare_pipes(pipes)[0]["config"]
    merged = deep_update(dict(pipe_class.get_default_config() or {}), injected)
    return validate_pipe_configuration(pipe_class, merged)


def _worker_effective_config(pipe_class, package_config: dict) -> dict:
    """What the worker executor computes - see executor.py's docstring for
    why this mirrors the local path exactly."""
    injected = inject_worker_device(
        dict(package_config), device=WORKER_DEVICE, dtype=WORKER_DTYPE, vram_limit_gb=WORKER_VRAM_GB,
    )
    merged = deep_update(dict(pipe_class.get_default_config() or {}), injected)
    return validate_pipe_configuration(pipe_class, merged)


def test_worker_injection_matches_local_backend_injection_for_an_unset_preset():
    """The preset never mentioned device/dtype/vram: both paths must land on
    this worker's values."""
    preset_config = {"steps": 30}
    package_config = deep_update(dict(PlainPipe.get_default_config()), dict(preset_config))

    local = _local_effective_config(PlainPipe, preset_config)
    worker = _worker_effective_config(PlainPipe, package_config)

    assert local == worker
    assert worker["device"] == WORKER_DEVICE
    assert worker["dtype"] == WORKER_DTYPE
    assert worker["vram_limit_gb"] == WORKER_VRAM_GB


def test_an_explicit_preset_value_wins_on_both_paths():
    """setdefault semantics: a preset that pins its own device must not be
    overridden by either the local backend or the worker."""
    preset_config = {"steps": 30, "device": "cpu"}
    package_config = deep_update(dict(PlainPipe.get_default_config()), dict(preset_config))

    local = _local_effective_config(PlainPipe, preset_config)
    worker = _worker_effective_config(PlainPipe, package_config)

    assert local == worker
    assert local["device"] == "cpu"
    assert worker["device"] == "cpu"


def test_inject_worker_device_never_mutates_its_input():
    original = {"steps": 1}
    inject_worker_device(original, device="cuda", dtype="fp16", vram_limit_gb=8.0)
    assert original == {"steps": 1}
