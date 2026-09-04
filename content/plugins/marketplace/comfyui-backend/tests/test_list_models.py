"""
Tests for ComfyUIBackend.list_models().

No network: aiohttp's ClientSession.get() is mocked to return canned JSON
payloads, mirroring what a live ComfyUI 0.26.0 server returns for
GET /models, GET /experiment/models/{folder}, and GET /models/{folder}.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from backend.comfyui_backend import ComfyUIBackend
from backend.comfyui_config import ComfyUIBackendConfig
from src.plugin_api.backends import (
    CONFIDENCE_NAME_ONLY,
    CONFIDENCE_REPORTED,
)


def make_backend() -> ComfyUIBackend:
    config = ComfyUIBackendConfig(
        id="comfy-1",
        name="Test ComfyUI",
        host="127.0.0.1",
        port=8188,
        secure=False,
        timeout_seconds=30,
    )
    return ComfyUIBackend(config)


class FakeResponse:
    """Stand-in for aiohttp's ClientResponse, used as an async context manager."""

    def __init__(self, payload=None, status=200, raise_exc=None):
        self._payload = payload
        self.status = status
        self._raise_exc = raise_exc

    async def json(self):
        return self._payload

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(), history=(), status=self.status
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Stand-in for aiohttp.ClientSession routing GET calls to a URL->response map.

    `routes` maps an exact URL to either a FakeResponse or a callable() ->
    FakeResponse (for endpoints that must raise each time they're hit).
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.requested_urls = []

    def get(self, url, *args, **kwargs):
        self.requested_urls.append(url)
        if url not in self.routes:
            raise AssertionError(f"Unexpected URL requested: {url}")
        route = self.routes[url]
        if callable(route) and not isinstance(route, FakeResponse):
            return route()
        return route

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def patch_session(routes: dict):
    """Patch aiohttp.ClientSession() to return a FakeSession serving `routes`."""
    session = FakeSession(routes)
    return patch("aiohttp.ClientSession", return_value=session), session


BASE = "http://127.0.0.1:8188"


@pytest.mark.asyncio
async def test_experiment_endpoint_yields_size_and_reported_confidence():
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["loras"]),
        f"{BASE}/experiment/models/loras": FakeResponse(
            [{"name": "foo.safetensors", "pathIndex": 0, "size": 12345}]
        ),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    entry = results[0]
    assert entry.model_type == "lora"
    assert entry.filename == "foo.safetensors"
    assert entry.ref == "foo.safetensors"
    assert entry.size == 12345
    assert entry.confidence == CONFIDENCE_REPORTED


@pytest.mark.asyncio
async def test_fallback_to_plain_models_endpoint_yields_name_only():
    backend = make_backend()

    def experiment_fails():
        raise aiohttp.ClientError("404 not found")

    routes = {
        f"{BASE}/models": FakeResponse(["loras"]),
        f"{BASE}/experiment/models/loras": experiment_fails,
        f"{BASE}/models/loras": FakeResponse(["bar.safetensors"]),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    entry = results[0]
    assert entry.filename == "bar.safetensors"
    assert entry.ref == "bar.safetensors"
    assert entry.size is None
    assert entry.confidence == CONFIDENCE_NAME_ONLY


@pytest.mark.asyncio
async def test_subdirectory_name_splits_ref_and_filename():
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["loras"]),
        f"{BASE}/experiment/models/loras": FakeResponse(
            [{"name": "style/detail.safetensors", "pathIndex": 0, "size": 100}]
        ),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    entry = results[0]
    assert entry.ref == "style/detail.safetensors"
    assert entry.filename == "detail.safetensors"


@pytest.mark.asyncio
async def test_duplicate_refs_same_size_collapse_to_one_entry():
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["upscale_models"]),
        f"{BASE}/experiment/models/upscale_models": FakeResponse(
            [
                {"name": "upscale.pth", "pathIndex": 0, "size": 555},
                {"name": "extra/upscale.pth", "pathIndex": 1, "size": 555},
            ]
        ),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    assert results[0].filename == "upscale.pth"


@pytest.mark.asyncio
async def test_same_filename_different_sizes_kept_as_two_entries():
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["checkpoints"]),
        f"{BASE}/experiment/models/checkpoints": FakeResponse(
            [
                {"name": "variant_a/model.safetensors", "pathIndex": 0, "size": 100},
                {"name": "variant_b/model.safetensors", "pathIndex": 1, "size": 200},
            ]
        ),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 2
    sizes = sorted(e.size for e in results)
    assert sizes == [100, 200]
    assert all(e.filename == "model.safetensors" for e in results)


@pytest.mark.asyncio
async def test_folders_absent_from_server_response_are_skipped():
    backend = make_backend()
    # Server only reports "vae" - "loras", "checkpoints" etc. are in our map but
    # not on this server, so they must not be requested at all.
    routes = {
        f"{BASE}/models": FakeResponse(["vae"]),
        f"{BASE}/experiment/models/vae": FakeResponse(
            [{"name": "vae_model.safetensors", "pathIndex": 0, "size": 999}]
        ),
    }
    patcher, session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    assert results[0].model_type == "vae"
    assert f"{BASE}/experiment/models/loras" not in session.requested_urls
    assert f"{BASE}/models/loras" not in session.requested_urls


@pytest.mark.asyncio
async def test_unrecognised_folders_are_skipped():
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["loras", "some_custom_node_folder"]),
        f"{BASE}/experiment/models/loras": FakeResponse(
            [{"name": "foo.safetensors", "pathIndex": 0, "size": 1}]
        ),
    }
    patcher, session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 1
    assert not any("some_custom_node_folder" in u for u in session.requested_urls)


@pytest.mark.asyncio
async def test_connection_error_raises_not_silent_empty_list():
    backend = make_backend()

    def top_level_fails():
        raise aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))

    routes = {f"{BASE}/models": top_level_fails}
    patcher, _session = patch_session(routes)
    with patcher:
        with pytest.raises(ConnectionError):
            await backend.list_models()


def test_supports_model_listing_is_true():
    backend = make_backend()
    assert backend.supports_model_listing() is True


@pytest.mark.asyncio
async def test_clip_and_text_encoders_folders_merge_into_text_encoder_type():
    """Both folders must land on `text_encoder`, the same model_type the core
    depot and every ComfyUI preset's CLIP field use - not the ComfyUI-only
    `clip` type, which the presets never select and the depot never reports."""
    backend = make_backend()
    routes = {
        f"{BASE}/models": FakeResponse(["text_encoders", "clip"]),
        f"{BASE}/experiment/models/text_encoders": FakeResponse(
            [{"name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "pathIndex": 0, "size": 1}]
        ),
        f"{BASE}/experiment/models/clip": FakeResponse(
            [{"name": "clip_l.safetensors", "pathIndex": 0, "size": 2}]
        ),
    }
    patcher, _session = patch_session(routes)
    with patcher:
        results = await backend.list_models()

    assert len(results) == 2
    assert all(entry.model_type == "text_encoder" for entry in results)
    filenames = {entry.filename for entry in results}
    assert filenames == {"qwen_2.5_vl_7b_fp8_scaled.safetensors", "clip_l.safetensors"}
