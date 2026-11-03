"""`RemoteNativeBackend.health_check`'s reading of the worker's CUDA claim.

A pod whose driver is too old for the worker image's torch build never errors:
torch falls back to CPU and the worker keeps answering handshakes. The only
signal core gets is the flag in the handshake, so a backend in that state must
read as degraded rather than healthy. The dispatch half of the same gate is
proven end to end against a real worker app in
`tests/features/remote_execution/test_native_remote_backend.py`
(`TestCudaPreGate`).
"""

from unittest.mock import patch

from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.platform.runtime.cuda_allocator import WORKER_RESTART_HINT
from src.platform.worker_protocol import WorkerCapabilitiesV1, WorkerInfoV1

DRIVER_TOO_OLD = "The NVIDIA driver on your system is too old (found version 12040)."
DAMAGED = WorkerCapabilitiesV1(features=("allocator_damaged",))


class StubTransport:
    def __init__(self, info: WorkerInfoV1):
        self._info = info

    async def handshake(self) -> WorkerInfoV1:
        return self._info


async def _health(**worker_info_fields) -> dict:
    info = WorkerInfoV1(worker_id="worker-1", provider="runpod", **worker_info_fields)
    backend = RemoteNativeBackend(NativeRemoteBackendConfig(
        id="remote-1", name="RunPod A100", base_url="http://fake-worker",
        worker_token="tok", enabled=True,
    ))
    with patch.object(RemoteNativeBackend, "_transport", return_value=StubTransport(info)):
        return await backend.health_check()


async def test_a_worker_that_cannot_reach_its_gpu_is_degraded():
    health = await _health(device="cuda", cuda_available=False, cuda_error=DRIVER_TOO_OLD)

    assert health["status"] == "degraded"
    assert "generation would run on CPU" in health["reason"]
    assert "driver is too old for the worker image's torch build" in health["reason"]


async def test_the_degraded_reason_carries_what_torch_actually_said():
    health = await _health(device="cuda", cuda_available=False, cuda_error=DRIVER_TOO_OLD)

    assert "found version 12040" in health["reason"]


async def test_a_worker_with_a_working_gpu_is_healthy():
    health = await _health(device="cuda", cuda_available=True)

    assert health["status"] == "healthy"
    assert "reason" not in health


async def test_a_worker_predating_the_cuda_fields_is_healthy():
    """Absence is unknown, not a denial - an older worker must keep working."""
    health = await _health()

    assert health["status"] == "healthy"
    assert "reason" not in health


async def test_a_deliberate_cpu_worker_is_not_degraded():
    health = await _health(device="cpu", cuda_available=False)

    assert health["status"] == "healthy"
    assert "reason" not in health


async def test_a_worker_advertising_allocator_damage_is_degraded_and_named():
    health = await _health(device="cuda", cuda_available=True, capabilities=DAMAGED)

    assert health["status"] == "degraded"
    assert health["reason"].startswith("Worker 'worker-1'")
    assert health["reason"].endswith(WORKER_RESTART_HINT)
    assert "Restart Backend" not in health["reason"]


async def test_a_cpu_worker_advertising_allocator_damage_is_not_degraded():
    health = await _health(device="cpu", cuda_available=None, capabilities=DAMAGED)

    assert health["status"] == "healthy"
    assert "reason" not in health


async def test_a_worker_advertising_no_damage_is_healthy():
    health = await _health(device="cuda", cuda_available=True, capabilities=WorkerCapabilitiesV1())

    assert health["status"] == "healthy"
    assert "reason" not in health


async def test_a_feature_token_this_host_does_not_know_changes_nothing():
    health = await _health(
        device="cuda", cuda_available=True, capabilities=WorkerCapabilitiesV1(features=("something_new",)),
    )

    assert health["status"] == "healthy"


async def test_a_handshake_from_a_worker_older_than_the_token_is_healthy():
    health = await _health()

    assert health["status"] == "healthy"
    assert "reason" not in health
