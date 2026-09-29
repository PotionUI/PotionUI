import asyncio
import importlib
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider

from src.features.providers.base_provider import ProviderNotFoundError
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


class _ApiResponse:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self):
        return self._payload


class _ApiSession:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self._responses.pop(0)


def _api(provider, *responses):
    session = _ApiSession(*responses)
    provider._get_session = AsyncMock(side_effect=AssertionError("the provider's own session must not be used"))
    return session


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://civitai.com/api/download/models/3361846", "model-fp16.safetensors"),
        ("https://civitai.com/api/download/models/3361846?type=Pruned%20Model", "model-pruned.safetensors"),
        ("https://civitai.com/models/123?modelVersionId=3361846", "model-fp16.safetensors"),
        ("https://civitai.com/models/123/slug?modelVersionId=3361846", "model-fp16.safetensors"),
    ],
)
@pytest.mark.asyncio
async def test_resolve_download_filename_from_version_metadata(provider, url, expected):
    api = _api(provider, _ApiResponse(200, VERSION_PAYLOAD))

    assert await provider.resolve_download_filename(api, url) == expected
    assert api.urls[0].endswith("/model-versions/3361846")


@pytest.mark.asyncio
async def test_resolve_download_filename_for_latest_page_url(provider):
    api = _api(provider, _ApiResponse(200, {"modelVersions": [VERSION_PAYLOAD]}))

    assert await provider.resolve_download_filename(api, "https://civitai.com/models/123/slug") == "model-fp16.safetensors"
    assert api.urls[0].endswith("/models/123")


@pytest.mark.parametrize(
    "url",
    [
        "https://civitai.com/api/download/models/3361846?type=Model&format=SafeTensor",
        "https://civitai.com/api/download/models/3361846?type=Missing",
    ],
)
@pytest.mark.asyncio
async def test_resolve_download_filename_is_none_when_a_file_selecting_query_matches_no_file(provider, url):
    api = _api(provider, _ApiResponse(200, VERSION_PAYLOAD))

    assert await provider.resolve_download_filename(api, url) is None


@pytest.mark.asyncio
async def test_resolve_download_filename_is_none_when_lookup_fails_or_url_foreign(provider):
    api = _api(provider, _ApiResponse(404))
    assert await provider.resolve_download_filename(api, DOWNLOAD_URLS[0]) is None
    api = _api(provider)
    assert await provider.resolve_download_filename(api, "https://example.com/api/download/models/1") is None
    assert api.urls == []


@pytest.mark.asyncio
async def test_resolve_download_filename_goes_through_the_rate_limited_lookup(provider):
    api = _api(provider, _ApiResponse(200, VERSION_PAYLOAD))
    provider._rate_limit = AsyncMock()

    await provider.resolve_download_filename(api, DOWNLOAD_URLS[0])

    provider._rate_limit.assert_awaited_once()


@pytest.mark.parametrize("url", PAGE_URLS_LATEST)
@pytest.mark.asyncio
async def test_page_url_of_a_model_with_no_versions_is_not_found_without_downloading(provider, url):
    provider._get_session = AsyncMock(return_value=_ApiSession(_ApiResponse(200, {"modelVersions": []})))
    session = _Session([])

    with pytest.raises(ProviderNotFoundError):
        await provider.prepare_download(session, url, {})

    assert session.calls == []


def test_filename_lookup_runs_on_another_loop_with_the_workers_session(provider):
    served = []

    async def handler(request):
        served.append(request.headers.get("Authorization"))
        return web.json_response(VERSION_PAYLOAD)

    async def start_server():
        app = web.Application()
        app.router.add_get("/model-versions/{vid}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        provider._session = aiohttp.ClientSession()
        return runner, site._server.sockets[0].getsockname()[1]

    result = {}

    def worker_thread(base):
        async def lookup():
            provider.BASE_URL = base
            async with aiohttp.ClientSession() as worker_session:
                result["name"] = await provider.resolve_download_filename(worker_session, DOWNLOAD_URLS[0])

        asyncio.run(lookup())

    async def serve_until_lookup_done(base_holder):
        thread = threading.Thread(target=worker_thread, args=(base_holder,))
        thread.start()
        while thread.is_alive():
            await asyncio.sleep(0.01)
        thread.join()

    loop = asyncio.new_event_loop()
    try:
        runner, port = loop.run_until_complete(start_server())
        loop.run_until_complete(serve_until_lookup_done(f"http://127.0.0.1:{port}"))
        loop.run_until_complete(provider._session.close())
        loop.run_until_complete(runner.cleanup())
    finally:
        loop.close()

    assert result["name"] == "model-fp16.safetensors"
    assert served == ["Bearer test-key"]
