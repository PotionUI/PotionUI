import pytest

from src.features.models import provider_info
from src.features.models.provider_info import ProviderInfoFetcher


class _Response:
    status = 404
    headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _RecordingSession:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _RecordingSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url):
        return _Response()


@pytest.mark.asyncio
async def test_preview_fetch_uses_the_shared_connector(monkeypatch):
    sentinel = object()
    _RecordingSession.instances.clear()
    monkeypatch.setattr(provider_info, "aiohttp_connector", lambda: sentinel)
    monkeypatch.setattr("aiohttp.ClientSession", _RecordingSession)

    result = await ProviderInfoFetcher.__new__(ProviderInfoFetcher)._fetch_media_bytes("https://example.com/p.png")

    assert result is None
    assert _RecordingSession.instances[0].kwargs["connector"] is sentinel
