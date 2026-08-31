"""Tests for HuggingFace provider's resolve_remote_url / resolve_remote_download.

Loaded by explicit file spec, not sys.path + `import provider.x` - see
test_huggingface_provider.py for why (the civitai-provider plugin's test
module claims the same "provider" package name).
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_provider_file = (
    Path(__file__).resolve().parents[3]
    / "content" / "plugins" / "marketplace" / "huggingface-provider" / "provider" / "huggingface_provider.py"
)
_spec = importlib.util.spec_from_file_location("huggingface_remote_download_module", _provider_file)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
HuggingFaceProvider = _mod.HuggingFaceProvider

from src.features.providers import (
    ProviderCapability,
    ProviderConnectionError,
    RemoteDownloadRef,
)


class _FakeProbeResponse:
    """Async context manager mimicking aiohttp's response for a Range probe."""

    def __init__(self, status, location=None):
        self.status = status
        self.headers = {"Location": location} if location else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeProbeSession:
    """Returns the queued response for each successive `.get(...)` call."""

    def __init__(self, responses):
        self._responses = list(responses)

    def get(self, url, headers=None, allow_redirects=False, **kwargs):
        return self._responses.pop(0)


@pytest.fixture
def provider():
    p = HuggingFaceProvider()
    p._initialized = True
    p._rate_limit_delay = 0
    p._last_request_time = 0
    return p


_URL = "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors"


def test_remote_download_capability_advertised():
    provider = HuggingFaceProvider()
    assert provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD)


@pytest.mark.asyncio
async def test_public_url_probe_redirect_resolves_to_the_original_url(provider):
    session = _FakeProbeSession([_FakeProbeResponse(302, "https://cdn-lfs-us-1.hf.co/redirect")])
    provider._get_session = AsyncMock(return_value=session)

    ref = await provider.resolve_remote_url(session, _URL)

    assert isinstance(ref, RemoteDownloadRef)
    assert ref.url == _URL
    assert ref.headers == {}


@pytest.mark.asyncio
async def test_gated_repo_with_no_token_configured_raises(provider):
    session = _FakeProbeSession([_FakeProbeResponse(401)])
    provider._get_session = AsyncMock(return_value=session)
    provider._api_key = None

    with pytest.raises(ProviderConnectionError):
        await provider.resolve_remote_url(session, _URL)


@pytest.mark.asyncio
async def test_gated_repo_with_a_token_follows_the_authed_redirect_to_a_signed_url(provider):
    session = _FakeProbeSession([
        _FakeProbeResponse(401),
        _FakeProbeResponse(302, "https://cdn-lfs-us-1.hf.co/signed/flux1-dev.safetensors?sig=abc"),
    ])
    provider._get_session = AsyncMock(return_value=session)
    provider._api_key = "hf_test_token"

    ref = await provider.resolve_remote_url(session, _URL)

    assert isinstance(ref, RemoteDownloadRef)
    assert ref.url == "https://cdn-lfs-us-1.hf.co/signed/flux1-dev.safetensors?sig=abc"
    assert ref.headers == {}
    assert "Authorization" not in ref.headers
    assert "hf_test_token" not in ref.url


@pytest.mark.asyncio
async def test_gated_repo_with_a_token_but_no_redirect_raises(provider):
    """A 200 on the authed probe means the bytes only flow with the header
    attached - that can't be handed to a remote worker."""
    session = _FakeProbeSession([
        _FakeProbeResponse(401),
        _FakeProbeResponse(200),
    ])
    provider._get_session = AsyncMock(return_value=session)
    provider._api_key = "hf_test_token"

    with pytest.raises(ProviderConnectionError):
        await provider.resolve_remote_url(session, _URL)


@pytest.mark.asyncio
async def test_resolve_remote_download_delegates_to_resolve_remote_url(provider):
    session = _FakeProbeSession([_FakeProbeResponse(302, "https://cdn-lfs-us-1.hf.co/redirect")])
    provider._get_session = AsyncMock(return_value=session)

    ref = await provider.resolve_remote_download("black-forest-labs/FLUX.1-dev", "main@flux1-dev.safetensors")

    assert ref.url == _URL
