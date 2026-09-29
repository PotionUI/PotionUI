import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider

from src.features.providers.registry import ProviderRegistry

CDN = "https://cdn.example.com/signed/model.safetensors?sig=abc"


class _Response:
    def __init__(self, status, location=None, payload=None):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, allow_redirects=False, **kwargs):
        self.calls.append((url, dict(headers or {})))
        return self._responses.pop(0)


@pytest.fixture
def provider():
    p = CivitaiProvider()
    p._initialized = True
    p._api_key = "test-key"
    p._rate_limit_delay = 0
    p._last_request_time = 0
    return p


@pytest.fixture
def registry(provider):
    with patch("src.features.providers.registry.PluginRepository"):
        reg = ProviderRegistry()
    reg._providers = {"civitai": provider}
    return reg


DOWNLOAD_URLS = [
    "https://civitai.com/api/download/models/777",
    "https://civitai.com/api/download/models/3361846",
    "https://civitai.com/api/download/models/777?type=Model&format=SafeTensor",
]
PAGE_URLS_WITH_VERSION = [
    "https://civitai.com/models/123?modelVersionId=777",
    "https://civitai.com/models/123/some-slug?modelVersionId=777",
]
PAGE_URLS_LATEST = [
    "https://civitai.com/models/123",
    "https://civitai.com/models/123/some-slug",
]


@pytest.mark.parametrize("url", DOWNLOAD_URLS + PAGE_URLS_WITH_VERSION + PAGE_URLS_LATEST)
def test_registry_claims_every_pasted_civitai_shape(registry, provider, url):
    assert registry.find_provider_for_url(url) is provider


@pytest.mark.parametrize("url", DOWNLOAD_URLS)
@pytest.mark.asyncio
async def test_download_url_request_carries_credentials(provider, url):
    session = _Session([_Response(302, CDN)])
    headers = {}

    resolved = await provider.prepare_download(session, url, headers)

    assert resolved == CDN
    assert session.calls[0][0] == url
    assert session.calls[0][1]["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("url", PAGE_URLS_WITH_VERSION)
@pytest.mark.asyncio
async def test_page_url_with_version_resolves_to_authenticated_download(provider, url):
    session = _Session([_Response(302, CDN)])

    resolved = await provider.prepare_download(session, url, {})

    assert resolved == CDN
    assert session.calls[0][0] == "https://civitai.com/api/download/models/777"
    assert session.calls[0][1]["Authorization"] == "Bearer test-key"


@pytest.mark.parametrize("url", PAGE_URLS_LATEST)
@pytest.mark.asyncio
async def test_page_url_without_version_resolves_latest_primary_file(provider, url):
    info = _Response(200, payload={
        "modelVersions": [{"files": [
            {"primary": False, "downloadUrl": "https://civitai.com/api/download/models/1"},
            {"primary": True, "downloadUrl": "https://civitai.com/api/download/models/888"},
        ]}]
    })
    provider._get_session = AsyncMock(return_value=_Session([info]))
    session = _Session([_Response(302, CDN)])

    resolved = await provider.prepare_download(session, url, {})

    assert resolved == CDN
    assert session.calls[0][0] == "https://civitai.com/api/download/models/888"
    assert session.calls[0][1]["Authorization"] == "Bearer test-key"


VERSION_PAYLOAD = {
    "files": [
        {"name": "model-fp16.safetensors", "primary": True, "downloadUrl": "https://civitai.com/api/download/models/3361846"},
        {"name": "model-pruned.safetensors", "downloadUrl": "https://civitai.com/api/download/models/3361846?type=Pruned%20Model"},
    ]
}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://civitai.com/api/download/models/3361846", "model-fp16.safetensors"),
        ("https://civitai.com/api/download/models/3361846?type=Pruned%20Model", "model-pruned.safetensors"),
        ("https://civitai.com/api/download/models/3361846?type=Model&format=SafeTensor", "model-fp16.safetensors"),
        ("https://civitai.com/models/123?modelVersionId=3361846", "model-fp16.safetensors"),
    ],
)
@pytest.mark.asyncio
async def test_resolve_download_filename_from_version_metadata(provider, url, expected):
    session = _Session([_Response(200, payload=VERSION_PAYLOAD)])

    assert await provider.resolve_download_filename(session, url) == expected
    assert session.calls[0][0].endswith("/model-versions/3361846")
    assert session.calls[0][1]["Authorization"] == "Bearer test-key"


@pytest.mark.asyncio
async def test_resolve_download_filename_for_latest_page_url(provider):
    payload = {"modelVersions": [VERSION_PAYLOAD]}
    session = _Session([_Response(200, payload=payload)])

    assert await provider.resolve_download_filename(session, "https://civitai.com/models/123/slug") == "model-fp16.safetensors"
    assert session.calls[0][0].endswith("/models/123")


@pytest.mark.asyncio
async def test_resolve_download_filename_is_none_when_lookup_fails_or_url_foreign(provider):
    assert await provider.resolve_download_filename(_Session([_Response(404)]), DOWNLOAD_URLS[0]) is None
    assert await provider.resolve_download_filename(_Session([]), "https://example.com/api/download/models/1") is None
