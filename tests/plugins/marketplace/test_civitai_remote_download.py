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

from src.features.providers import (
    ProviderCapability,
    ProviderConnectionError,
    ProviderNotFoundError,
    RemoteDownloadRef,
)


class _FakeJsonResponse:
    """Async context manager mimicking aiohttp's response for a JSON GET."""

    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload
        self.headers = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self._payload


class _FakeByHashSession:
    """Returns the by-hash JSON response first, then the redirect chain
    `prepare_download` drives against `.get(...)`."""

    def __init__(self, by_hash_response, redirect_responses):
        self._by_hash_response = by_hash_response
        self._redirect_responses = list(redirect_responses)

    def get(self, url, headers=None, allow_redirects=True, **kwargs):
        if "by-hash" in url:
            return self._by_hash_response
        return self._redirect_responses.pop(0)


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


def test_remote_download_by_hash_capability_advertised():
    provider = CivitaiProvider()
    assert provider.get_metadata().has_capability(ProviderCapability.REMOTE_DOWNLOAD_BY_HASH)


_TARGET_SHA256 = "b" * 64


def _by_hash_payload(*, matching_index: int):
    """A model-version record with two files - only `matching_index` carries
    the hash we searched for, mimicking a fp16/fp32 pair on the same version."""
    files = [
        {
            "downloadUrl": "https://civitai.com/api/download/models/999?type=Model&format=SafeTensor",
            "hashes": {"SHA256": "a" * 64},
            "sizeKB": 100.0,
        },
        {
            "downloadUrl": "https://civitai.com/api/download/models/999?type=Model&format=SafeTensor&size=pruned",
            "hashes": {"SHA256": _TARGET_SHA256.upper()},
            "sizeKB": 50.0,
        },
    ]
    if matching_index != 1:
        files[0], files[1] = files[1], files[0]
    return {"id": 999, "modelId": 111, "files": files}


@pytest.mark.asyncio
async def test_resolve_remote_download_by_hash_selects_the_matching_file_not_the_first(provider):
    """Only the second file's hash matches - the first must never be picked."""
    session = _FakeByHashSession(
        by_hash_response=_FakeJsonResponse(200, _by_hash_payload(matching_index=1)),
        redirect_responses=[
            _FakeRedirectResponse(
                302, "https://civitai-delivery-worker.example.com/signed/pruned.safetensors?sig=abc"
            ),
        ],
    )
    provider._get_session = AsyncMock(return_value=session)

    ref = await provider.resolve_remote_download_by_hash(_TARGET_SHA256)

    assert isinstance(ref, RemoteDownloadRef)
    assert ref.url == "https://civitai-delivery-worker.example.com/signed/pruned.safetensors?sig=abc"
    assert ref.headers == {}
    assert ref.size_hint == 50 * 1024


@pytest.mark.asyncio
async def test_resolve_remote_download_by_hash_raises_when_no_file_matches(provider):
    """A 200 response whose files list has no matching hash is a miss, not a
    fallback to the first (or 'primary') file - CivitAI's by-hash endpoint has
    returned the wrong record before."""
    session = _FakeByHashSession(
        by_hash_response=_FakeJsonResponse(200, {"id": 999, "files": [
            {"downloadUrl": "https://civitai.com/api/download/models/999", "hashes": {"SHA256": "c" * 64}},
        ]}),
        redirect_responses=[],
    )
    provider._get_session = AsyncMock(return_value=session)

    with pytest.raises(ProviderNotFoundError):
        await provider.resolve_remote_download_by_hash(_TARGET_SHA256)


@pytest.mark.asyncio
async def test_resolve_remote_download_by_hash_miss_raises_not_found(provider):
    session = _FakeByHashSession(by_hash_response=_FakeJsonResponse(404), redirect_responses=[])
    provider._get_session = AsyncMock(return_value=session)

    with pytest.raises(ProviderNotFoundError):
        await provider.resolve_remote_download_by_hash(_TARGET_SHA256)
