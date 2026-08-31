"""Tests for CivitAI provider's resolve_remote_download."""

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# The plugin lives under a hyphenated directory that cannot be imported normally.
_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider

from src.features.providers import ProviderCapability, ProviderConnectionError, RemoteDownloadRef


class _FakeRedirectResponse:
    """Async context manager mimicking aiohttp's response for a redirect chain."""

    def __init__(self, status, location=None):
        self.status = status
        self.headers = {"Location": location} if location else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeRedirectSession:
    """Returns the queued response for each successive `.get(...)` call."""

    def __init__(self, responses):
        self._responses = list(responses)

    def get(self, url, headers=None, allow_redirects=False, **kwargs):
        return self._responses.pop(0)


@pytest.fixture
def provider():
    p = CivitaiProvider()
    p._initialized = True
    p._api_key = "test-key"
    p._rate_limit_delay = 0
    p._last_request_time = 0
    return p


@pytest.mark.asyncio
async def test_resolve_remote_download_returns_presigned_cdn_url(provider):
    """A valid CivitAI redirect resolves to the CDN URL with no headers."""
    session = _FakeRedirectSession([
        _FakeRedirectResponse(302, "https://civitai-delivery-worker.example.com/signed/model.safetensors?sig=abc"),
    ])
    provider._get_session = AsyncMock(return_value=session)

    ref = await provider.resolve_remote_download("12345", "67890")

    assert isinstance(ref, RemoteDownloadRef)
    assert ref.url == "https://civitai-delivery-worker.example.com/signed/model.safetensors?sig=abc"
    assert ref.headers == {}


@pytest.mark.asyncio
async def test_resolve_remote_download_rejects_unresolved_civitai_url(provider):
    """No CDN redirect (e.g. bad key) must never hand back a civitai.com URL carrying auth."""
    session = _FakeRedirectSession([
        _FakeRedirectResponse(200),
    ])
    provider._get_session = AsyncMock(return_value=session)

    with pytest.raises(ProviderConnectionError):
        await provider.resolve_remote_download("12345", "67890")


def test_remote_download_capability_advertised():
    provider = CivitaiProvider()
    assert provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD)
