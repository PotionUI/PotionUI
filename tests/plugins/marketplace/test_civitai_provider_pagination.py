"""Tests for CivitAI provider fetch_image_prompts pagination and model validation."""

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The plugin lives under a hyphenated directory that cannot be imported normally.
_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider

from src.features.providers import ProviderNotFoundError


def _make_civitai_response(items, next_cursor=None):
    """Build a fake CivitAI /images JSON response."""
    metadata = {}
    if next_cursor:
        metadata["nextCursor"] = next_cursor
    return {
        "items": [
            {
                "id": item_id,
                "meta": {"prompt": f"prompt {item_id}", "negativePrompt": "bad"},
                "stats": {"heartCount": 1, "likeCount": 0, "laughCount": 0, "cryCount": 0, "commentCount": 0},
                "width": 1024,
                "height": 1024,
            }
            for item_id in items
        ],
        "metadata": metadata,
    }


class _FakeResponse:
    """Async context manager mimicking aiohttp's response."""

    def __init__(self, data, url="https://civitai.com/api/v1/images"):
        self.status = 200
        self._data = data
        self.url = url

    async def json(self):
        return self._data

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


def _valid_model_mock():
    """Return an AsyncMock that simulates a valid model on CivitAI."""
    return AsyncMock(return_value={"id": 12345, "name": "Test Model"})


def _valid_version_mock():
    """Return an AsyncMock that simulates a valid version on CivitAI."""
    return AsyncMock(return_value={"id": 67890, "modelId": 12345})


# ── Pagination tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_all_paginates_through_all_pages(provider):
    """When fetch_all=True, the provider should follow nextCursor until exhausted."""
    pages = [
        _make_civitai_response([1, 2, 3], next_cursor="cursor_abc"),
        _make_civitai_response([4, 5, 6], next_cursor="cursor_def"),
        _make_civitai_response([7, 8], next_cursor=None),
    ]
    call_index = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal call_index
        resp = _FakeResponse(pages[call_index])
        call_index += 1
        return resp

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(
            model_id="12345",
            sort="Most Reactions",
            period="AllTime",
            limit=100,
            fetch_all=True,
        )

    assert len(items) == 8
    assert call_index == 3


@pytest.mark.asyncio
async def test_fetch_all_preserves_original_params(provider):
    """Original query params (modelId, sort, period, nsfw) must be sent on every page."""
    pages = [
        _make_civitai_response([1], next_cursor="cur_1"),
        _make_civitai_response([2], next_cursor=None),
    ]
    captured_params = []

    def _mock_get(url, params=None, timeout=None):
        captured_params.append(dict(params) if params else {})
        return _FakeResponse(pages[len(captured_params) - 1])

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        await provider.fetch_image_prompts(
            model_id="999",
            sort="Newest",
            period="Month",
            limit=50,
            nsfw=False,
            fetch_all=True,
        )

    assert len(captured_params) == 2

    # When fetch_all=True, per-page limit is 200 for efficiency
    assert captured_params[0]["modelId"] == "999"
    assert captured_params[0]["sort"] == "Newest"
    assert captured_params[0]["period"] == "Month"
    assert captured_params[0]["limit"] == 200
    assert "cursor" not in captured_params[0]

    # Second request: original params PLUS cursor
    assert captured_params[1]["modelId"] == "999"
    assert captured_params[1]["sort"] == "Newest"
    assert captured_params[1]["period"] == "Month"
    assert captured_params[1]["limit"] == 200
    assert captured_params[1]["cursor"] == "cur_1"


@pytest.mark.asyncio
async def test_fetch_all_false_returns_single_page(provider):
    """When fetch_all=False, only one page should be fetched even if nextCursor exists."""
    page = _make_civitai_response([1, 2, 3], next_cursor="more_data")
    call_count = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(
            model_id="12345",
            fetch_all=False,
        )

    assert len(items) == 3
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_all_stops_when_no_cursor(provider):
    """Pagination stops naturally when the response has no nextCursor."""
    page = _make_civitai_response([1], next_cursor=None)
    call_count = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(fetch_all=True)

    assert len(items) == 1
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_all_respects_max_total(provider):
    """When fetch_all=True, limit acts as max_total cap on items collected."""
    pages = [
        _make_civitai_response([1, 2, 3], next_cursor="cur_1"),
        _make_civitai_response([4, 5, 6], next_cursor="cur_2"),
        _make_civitai_response([7, 8, 9], next_cursor="cur_3"),
        _make_civitai_response([10, 11, 12], next_cursor=None),
    ]
    call_index = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal call_index
        resp = _FakeResponse(pages[call_index])
        call_index += 1
        return resp

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(
            model_id="12345",
            limit=7,
            fetch_all=True,
        )

    assert len(items) == 7
    # Should stop after 3rd page (9 items >= 7 cap)
    assert call_index == 3


@pytest.mark.asyncio
async def test_fetch_all_uses_200_page_size(provider):
    """When fetch_all=True, per-page limit should be 200 for efficiency."""
    page = _make_civitai_response([1], next_cursor=None)
    captured_params = []

    def _mock_get(url, params=None, timeout=None):
        captured_params.append(dict(params) if params else {})
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        await provider.fetch_image_prompts(
            model_id="12345",
            limit=50,
            fetch_all=True,
        )

    assert captured_params[0]["limit"] == 200


# ── Model ID validation tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_model_id_raises_error(provider):
    """If model ID not found as model or version, raise ProviderNotFoundError."""
    with patch.object(provider, "_validate_model_id", AsyncMock(return_value=None)), \
         patch.object(provider, "_validate_model_version_id", AsyncMock(return_value=None)):
        with pytest.raises(ProviderNotFoundError, match="not found on CivitAI"):
            await provider.fetch_image_prompts(model_id="999999")


@pytest.mark.asyncio
async def test_version_id_as_model_id_auto_corrects(provider):
    """If model_id is actually a version ID, auto-correct and use modelVersionId."""
    page = _make_civitai_response([10, 11, 12])
    captured_params = []

    def _mock_get(url, params=None, timeout=None):
        captured_params.append(dict(params) if params else {})
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", AsyncMock(return_value=None)), \
         patch.object(provider, "_validate_model_version_id", _valid_version_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(model_id="456789")

    assert len(items) == 3
    # Should have used modelVersionId, not modelId
    assert "modelVersionId" in captured_params[0]
    assert captured_params[0]["modelVersionId"] == "456789"
    assert "modelId" not in captured_params[0]


@pytest.mark.asyncio
async def test_valid_model_id_passes_directly(provider):
    """Valid model ID is used directly without fallback."""
    page = _make_civitai_response([1, 2])
    call_count = 0

    def _mock_get(url, params=None, timeout=None):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", _valid_model_mock()), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(model_id="12345")

    assert len(items) == 2
    assert call_count == 1


@pytest.mark.asyncio
async def test_no_validation_when_model_version_id_set(provider):
    """Validation is skipped when model_version_id is explicitly provided."""
    page = _make_civitai_response([1])
    validate_mock = AsyncMock()

    def _mock_get(url, params=None, timeout=None):
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", validate_mock), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts(
            model_id="12345",
            model_version_id="67890",
        )

    assert len(items) == 1
    validate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_no_validation_without_model_id(provider):
    """No validation call when no model_id is provided."""
    page = _make_civitai_response([1])
    validate_mock = AsyncMock()

    def _mock_get(url, params=None, timeout=None):
        return _FakeResponse(page)

    mock_session = MagicMock()
    mock_session.get = _mock_get

    with patch.object(provider, "_validate_model_id", validate_mock), \
         patch.object(provider, "_get_session", AsyncMock(return_value=mock_session)):
        items = await provider.fetch_image_prompts()

    assert len(items) == 1
    validate_mock.assert_not_called()
