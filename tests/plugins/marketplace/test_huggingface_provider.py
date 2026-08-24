"""Tests for the HuggingFace marketplace provider.

Mirrors tests/plugins/marketplace/test_civitai_provider_pagination.py: the
plugin lives under a hyphenated directory that can't be imported normally,
so it's loaded via importlib with the plugin dir prepended to sys.path.
No real network calls are made - aiohttp is mocked throughout.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Loaded by explicit file spec (not sys.path + `import provider.x`) because the
# civitai-provider plugin's test module claims the same "provider" package name
# under sys.path; importing both in one run would silently resolve to whichever
# directory got there first.
_provider_file = (
    Path(__file__).resolve().parents[3]
    / "content" / "plugins" / "marketplace" / "huggingface-provider" / "provider" / "huggingface_provider.py"
)
_spec = importlib.util.spec_from_file_location("huggingface_provider_module", _provider_file)
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)
HuggingFaceProvider = _mod.HuggingFaceProvider

from src.features.providers import (
    ProviderCapability,
    ProviderConnectionError,
    ProviderNotFoundError,
    ProviderRateLimitError,
)


class _FakeResponse:
    """Async context manager mimicking aiohttp's response."""

    def __init__(self, data, status=200, headers=None):
        self.status = status
        self._data = data
        self.headers = headers or {}

    async def json(self):
        return self._data

    async def text(self):
        return str(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_session(response: _FakeResponse):
    """Build a fake aiohttp.ClientSession whose .get() returns `response`."""
    session = MagicMock()
    session.closed = False  # unconfigured MagicMock attrs are truthy, which would
                             # make _get_session() think this mock is closed and
                             # silently replace it with a real aiohttp.ClientSession.
    session.get = MagicMock(return_value=response)
    return session


@pytest.fixture
async def provider():
    p = HuggingFaceProvider()
    await p.initialize({})
    p._rate_limit_delay = 0
    p._last_request_time = 0
    return p


# ── Metadata / capabilities ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_metadata_does_not_advertise_hash_lookup(provider):
    """HuggingFace has no hash-reverse-index API; HASH_LOOKUP must not be advertised."""
    metadata = provider.get_metadata()
    assert not metadata.has_capability(ProviderCapability.HASH_LOOKUP)
    assert metadata.has_capability(ProviderCapability.SEARCH)
    assert metadata.has_capability(ProviderCapability.DOWNLOAD_URL)


@pytest.mark.asyncio
async def test_get_model_by_hash_always_none(provider):
    result = await provider.get_model_by_hash("deadbeef" * 8)
    assert result is None


# ── initialize / headers ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_without_key_has_no_auth_header():
    p = HuggingFaceProvider()
    await p.initialize({})
    assert 'Authorization' not in p.get_download_headers()


@pytest.mark.asyncio
async def test_initialize_with_key_sets_auth_header():
    p = HuggingFaceProvider()
    await p.initialize({'api_key': 'hf_secrettoken'})
    headers = p.get_download_headers()
    assert headers['Authorization'] == 'Bearer hf_secrettoken'


@pytest.mark.asyncio
async def test_get_authenticated_download_url_is_passthrough(provider):
    url = "https://huggingface.co/org/repo/resolve/main/model.safetensors"
    assert provider.get_authenticated_download_url(url) == url


def test_download_headers_never_logged_contains_no_plaintext_key_by_default():
    # Sanity: without a key configured, no secret material appears in headers.
    p = HuggingFaceProvider()
    p._headers = {'User-Agent': 'PotionUI/1.0'}
    p._api_key = None
    assert p.get_download_headers() == {'User-Agent': 'PotionUI/1.0'}


# ── ref parsing ──────────────────────────────────────────────────────


def test_parse_version_id_with_revision():
    revision, filepath = HuggingFaceProvider._parse_version_id("v1.0@model.safetensors")
    assert revision == "v1.0"
    assert filepath == "model.safetensors"


def test_parse_version_id_without_revision_defaults_main():
    revision, filepath = HuggingFaceProvider._parse_version_id("model.safetensors")
    assert revision == "main"
    assert filepath == "model.safetensors"


def test_parse_version_id_with_subdirectory():
    revision, filepath = HuggingFaceProvider._parse_version_id("main@unet/diffusion_pytorch_model.safetensors")
    assert revision == "main"
    assert filepath == "unet/diffusion_pytorch_model.safetensors"


def test_build_resolve_url_quotes_path_segments():
    url = HuggingFaceProvider._build_resolve_url("org/repo name", "main", "sub dir/model.safetensors")
    assert url == "https://huggingface.co/org/repo name/resolve/main/sub%20dir/model.safetensors"


def test_matches_url(provider):
    assert provider.matches_url("https://huggingface.co/org/repo/resolve/main/model.safetensors")
    assert not provider.matches_url("https://civitai.com/api/download/models/123")


# ── get_download_url ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_download_url_with_explicit_ref(provider):
    url = await provider.get_download_url("org/repo", "main@model.safetensors")
    assert url == "https://huggingface.co/org/repo/resolve/main/model.safetensors"


@pytest.mark.asyncio
async def test_get_download_url_without_ref_raises_naming_repo(provider):
    """A repo has no single "primary" file - an omitted ref must not fall back
    to a guessed file. No session is set on `provider`, so this also proves no
    network call is made: a guess would need one to list the repo's files."""
    with pytest.raises(ProviderNotFoundError, match="org/repo"):
        await provider.get_download_url("org/repo")


@pytest.mark.asyncio
async def test_get_download_url_empty_ref_raises_naming_repo(provider):
    with pytest.raises(ProviderNotFoundError, match="org/repo"):
        await provider.get_download_url("org/repo", "")


@pytest.mark.asyncio
async def test_get_download_url_empty_filepath_in_ref_raises(provider):
    with pytest.raises(ProviderNotFoundError):
        await provider.get_download_url("org/repo", "main@")


@pytest.mark.asyncio
async def test_get_download_url_uninitialized_raises():
    p = HuggingFaceProvider()
    with pytest.raises(ProviderConnectionError):
        await p.get_download_url("org/repo", "main@model.safetensors")


# ── search_models ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_models_parses_results(provider):
    data = [
        {
            "id": "black-forest-labs/FLUX.1-dev",
            "downloads": 12345,
            "tags": ["diffusers", "text-to-image"],
            "siblings": [{"rfilename": "preview.png"}],
        },
        {
            "id": "someuser/some-lora",
            "downloads": 42,
            "tags": ["lora"],
            "siblings": [],
        },
    ]
    response = _FakeResponse(data)
    provider._session = _make_session(response)

    results = await provider.search_models("flux", limit=20, offset=0)

    assert len(results) == 2
    assert results[0].provider_id == "huggingface"
    assert results[0].provider_model_id == "black-forest-labs/FLUX.1-dev"
    assert results[0].thumbnail_url == (
        "https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/preview.png"
    )
    assert results[1].model_type == "lora"
    assert results[1].thumbnail_url is None


@pytest.mark.asyncio
async def test_search_models_applies_offset_and_limit(provider):
    data = [{"id": f"org/repo-{i}", "tags": [], "siblings": []} for i in range(5)]
    response = _FakeResponse(data)
    provider._session = _make_session(response)

    results = await provider.search_models("x", limit=2, offset=2)

    assert [r.provider_model_id for r in results] == ["org/repo-2", "org/repo-3"]


@pytest.mark.asyncio
async def test_search_models_maps_model_type_to_filter(provider):
    response = _FakeResponse([])
    session = _make_session(response)
    provider._session = session

    await provider.search_models("x", model_type="lora")

    _, kwargs = session.get.call_args
    assert kwargs["params"]["filter"] == "lora"


@pytest.mark.asyncio
async def test_search_models_rate_limited(provider):
    response = _FakeResponse({}, status=429, headers={"Retry-After": "15"})
    provider._session = _make_session(response)

    with pytest.raises(ProviderRateLimitError):
        await provider.search_models("x")


@pytest.mark.asyncio
async def test_search_models_uninitialized_raises():
    p = HuggingFaceProvider()
    with pytest.raises(ProviderConnectionError):
        await p.search_models("x")


# ── test_connection ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connection_without_key_hits_public_endpoint(provider):
    response = _FakeResponse([])
    session = _make_session(response)
    provider._session = session

    ok = await provider.test_connection()

    assert ok is True
    args, kwargs = session.get.call_args
    assert args[0] == f"{provider.API_URL}/models"


@pytest.mark.asyncio
async def test_connection_with_key_hits_whoami(provider):
    provider._api_key = "hf_secrettoken"
    response = _FakeResponse({"name": "someone"})
    session = _make_session(response)
    provider._session = session

    ok = await provider.test_connection()

    assert ok is True
    args, kwargs = session.get.call_args
    assert args[0] == f"{provider.API_URL}/whoami-v2"


@pytest.mark.asyncio
async def test_connection_uninitialized_returns_false():
    p = HuggingFaceProvider()
    assert await p.test_connection() is False


# ── settings schema ──────────────────────────────────────────────────


def test_settings_schema_marks_api_key_as_password(provider):
    schema = provider.get_settings_schema()
    assert schema["properties"]["api_key"]["format"] == "password"
    assert schema["required"] == []
