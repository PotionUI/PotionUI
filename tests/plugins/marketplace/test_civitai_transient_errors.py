import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider

from src.features.providers import ProviderConnectionError, ProviderRateLimitError

SHA = "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"


class _FakeResponse:
    def __init__(self, data=None, status=200, headers=None, text_body=None):
        self.status = status
        self._data = data
        self.headers = headers or {}
        self._text_body = text_body if text_body is not None else str(data)

    async def json(self):
        return self._data

    async def text(self):
        return self._text_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def provider():
    p = CivitaiProvider()
    p._initialized = True
    p._rate_limit_delay = 0
    p._last_request_time = 0
    return p


def _with_session(provider, dispatch):
    mock_session = MagicMock()
    mock_session.get = dispatch
    return patch.object(provider, "_get_session", AsyncMock(return_value=mock_session))


@pytest.mark.asyncio
async def test_429_raises_a_rate_limit_error_carrying_retry_after(provider):
    def dispatch(url, params=None, timeout=None):
        return _FakeResponse(status=429, headers={"Retry-After": "12"})

    with _with_session(provider, dispatch):
        with pytest.raises(ProviderRateLimitError) as excinfo:
            await provider.get_model_by_hash(SHA)

    assert excinfo.value.retry_after == 12.0


@pytest.mark.asyncio
async def test_missing_retry_after_falls_back_to_sixty_seconds(provider):
    def dispatch(url, params=None, timeout=None):
        return _FakeResponse(status=429)

    with _with_session(provider, dispatch):
        with pytest.raises(ProviderRateLimitError) as excinfo:
            await provider.get_model_by_hash(SHA)

    assert excinfo.value.retry_after == 60.0


@pytest.mark.asyncio
async def test_server_error_is_a_connection_error_not_a_silent_not_found(provider):
    """A 5xx used to be swallowed into a plain `None` (the same shape as a
    genuine "not on CivitAI"), so a caller had no way to retry it or report
    what actually went wrong. It must now surface as a connection error."""

    def dispatch(url, params=None, timeout=None):
        return _FakeResponse(status=503, text_body="upstream unavailable")

    with _with_session(provider, dispatch):
        with pytest.raises(ProviderConnectionError):
            await provider.get_model_by_hash(SHA)


@pytest.mark.asyncio
async def test_a_real_404_is_still_a_plain_not_found(provider):
    def dispatch(url, params=None, timeout=None):
        return _FakeResponse(status=404)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is None


@pytest.mark.asyncio
async def test_timeout_raises_a_connection_error(provider):
    def dispatch(url, params=None, timeout=None):
        raise asyncio.TimeoutError()

    with _with_session(provider, dispatch):
        with pytest.raises(ProviderConnectionError):
            await provider.get_model_by_hash(SHA)
