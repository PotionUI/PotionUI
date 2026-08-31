"""Belt-and-braces guard on `RemoteNativeBackend.start_generation`: an
unconfigured backend (no worker URL/token - see
`NativeRemoteBackendConfig.is_configured`) must never be dispatched to, even
if it somehow reached this point despite the enable guard in
`BackendController` (disabled/unconfigured backends aren't selectable for
generation in the first place - this is the second line of defense).
"""

import pytest

from src.features.backends.backend_config import NativeRemoteBackendConfig
from src.features.backends.native_remote_backend import RemoteNativeBackend


async def test_start_generation_rejects_an_unconfigured_backend():
    config = NativeRemoteBackendConfig(id="remote-1", name="RunPod A100", enabled=False)
    backend = RemoteNativeBackend(config)

    with pytest.raises(RuntimeError, match="no worker URL/token configured"):
        await backend.start_generation({"pipes": [{"pipe_id": "p1"}]}, emit=lambda output: None)


async def test_start_generation_proceeds_past_the_guard_when_configured():
    """A configured backend must clear the guard and fail for the NEXT reason
    instead (no pipe catalog bound) - proving the guard doesn't false-positive
    on a legitimately configured backend."""
    config = NativeRemoteBackendConfig(
        id="remote-1", name="RunPod A100", base_url="http://fake-worker", worker_token="tok", enabled=True,
    )
    backend = RemoteNativeBackend(config)

    with pytest.raises(RuntimeError, match="no pipe catalog bound"):
        await backend.start_generation({"pipes": [{"pipe_id": "p1"}]}, emit=lambda output: None)
