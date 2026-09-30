import importlib
import sys
from pathlib import Path

import pytest

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")


class _RecordingSession:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        _RecordingSession.instances.append(self)


@pytest.mark.asyncio
async def test_session_uses_the_shared_connector(monkeypatch):
    sentinel = object()
    _RecordingSession.instances.clear()
    monkeypatch.setattr(_mod, "aiohttp_connector", lambda: sentinel)
    monkeypatch.setattr(_mod.aiohttp, "ClientSession", _RecordingSession)

    provider = _mod.CivitaiProvider()
    session = await provider._get_session()

    assert session.kwargs["connector"] is sentinel
