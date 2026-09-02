"""`RemoteNativeBackend.health_check`'s reading of `WorkerUnreachableError` -
its `detail` must carry the transport's classification (`"not_running"` for a
gateway answering on a stopped/still-starting worker's behalf, `"connect"` for
a genuine connect failure) so the backend card can tell "down" from
"misconfigured" (a `WorkerProtocolError`, handled separately as `status: "error"`).
"""

from unittest.mock import patch

from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.native_remote_backend import RemoteNativeBackend
from src.features.remote_execution.transport import WorkerUnreachableError


class RaisingTransport:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def handshake(self):
        raise self._exc


async def _health(exc: Exception) -> dict:
    backend = RemoteNativeBackend(NativeRemoteBackendConfig(
        id="remote-1", name="RunPod A100", base_url="http://fake-worker", worker_token="tok", enabled=True,
    ))
    with patch.object(RemoteNativeBackend, "_transport", return_value=RaisingTransport(exc)):
        return await backend.health_check()


async def test_a_stopped_worker_reports_offline_with_a_not_running_detail():
    exc = WorkerUnreachableError(
        "worker is not running (gateway answered HTTP 404 for /v1/worker)", reason="not_running",
    )

    health = await _health(exc)

    assert health["status"] == "offline"
    assert health["detail"] == "not_running"


async def test_a_genuine_connect_failure_reports_offline_with_a_connect_detail():
    exc = WorkerUnreachableError("could not reach worker at http://fake-worker: connection refused")

    health = await _health(exc)

    assert health["status"] == "offline"
    assert health["detail"] == "connect"
