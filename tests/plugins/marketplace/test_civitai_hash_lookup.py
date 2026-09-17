import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_provider_dir = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "civitai-provider"
sys.path.insert(0, str(_provider_dir))
_mod = importlib.import_module("provider.civitai_provider")
CivitaiProvider = _mod.CivitaiProvider
_html_to_text = _mod._html_to_text

SHA = "31e35c80fc4829d14f90153f4c74cd59c90b779f6afe05a74cd6120b893f7e5b"


class _FakeResponse:
    def __init__(self, data, status=200):
        self.status = status
        self._data = data

    async def json(self):
        return self._data

    async def text(self):
        return str(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _by_hash_data(files=None, description=None, trained_words=None):
    return {
        "id": 67890,
        "modelId": 12345,
        "model": {"id": 12345, "name": "Test Model", "type": "Checkpoint", "nsfw": False},
        "description": description,
        "trainedWords": trained_words or [],
        "baseModel": "SDXL 1.0",
        "images": [],
        "files": files if files is not None else [
            {"primary": True, "downloadUrl": "https://x/download", "hashes": {"SHA256": SHA.upper()}}
        ],
    }


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
async def test_returns_none_when_no_file_matches_the_requested_hash(provider):
    by_hash = _by_hash_data(files=[{"primary": True, "downloadUrl": "https://x", "hashes": {"SHA256": "0" * 64}}])
    calls = []

    def dispatch(url, params=None, timeout=None):
        calls.append(url)
        return _FakeResponse(by_hash)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_fills_description_from_version_when_present(provider):
    by_hash = _by_hash_data(description="<p>Version <strong>desc</strong></p><p>second para</p>")
    model_data = {"id": 12345, "name": "Test Model", "description": "<p>Model desc</p>", "tags": ["x", "y"]}

    def dispatch(url, params=None, timeout=None):
        if "by-hash" in url:
            return _FakeResponse(by_hash)
        return _FakeResponse(model_data)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is not None
    assert result.description == "Version desc\n\nsecond para"


@pytest.mark.asyncio
async def test_falls_back_to_model_description_when_version_description_is_empty(provider):
    by_hash = _by_hash_data(description=None)
    model_data = {"id": 12345, "name": "Test Model", "description": "<p>Model &amp; desc</p>", "tags": []}

    def dispatch(url, params=None, timeout=None):
        if "by-hash" in url:
            return _FakeResponse(by_hash)
        return _FakeResponse(model_data)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is not None
    assert result.description == "Model & desc"


@pytest.mark.asyncio
async def test_tags_merge_model_tags_with_trained_words_deduped(provider):
    by_hash = _by_hash_data(trained_words=["y", "z"])
    model_data = {"id": 12345, "name": "Test Model", "description": None, "tags": ["x", "y"]}

    def dispatch(url, params=None, timeout=None):
        if "by-hash" in url:
            return _FakeResponse(by_hash)
        return _FakeResponse(model_data)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is not None
    assert result.tags == ["x", "y", "z"]


@pytest.mark.asyncio
async def test_enrichment_failure_does_not_fail_the_lookup(provider):
    by_hash = _by_hash_data(description=None, trained_words=["a"])

    def dispatch(url, params=None, timeout=None):
        if "by-hash" in url:
            return _FakeResponse(by_hash)
        return _FakeResponse({}, status=404)

    with _with_session(provider, dispatch):
        result = await provider.get_model_by_hash(SHA)

    assert result is not None
    assert result.description is None
    assert result.tags == ["a"]


class TestHtmlToText:
    def test_strips_tags_and_converts_paragraphs_to_blank_lines(self):
        assert _html_to_text("<p>one</p><p>two</p>") == "one\n\ntwo"

    def test_unescapes_entities(self):
        assert _html_to_text("<p>a &amp; b &lt;c&gt;</p>") == "a & b <c>"

    def test_none_and_empty_input(self):
        assert _html_to_text(None) is None
        assert _html_to_text("") == ""
