"""Tests for the `comfyui_node`/`comfyui_model` preset requirement checkers
(backend/requirements.py) and their `manifest.yml` registration.

No network: aiohttp's `ClientSession.get()` is faked the same way
test_list_models.py fakes ComfyUIBackend.list_models()'s HTTP calls.
"""

from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
import yaml

from backend import requirements as req_mod
from backend.requirements import (
    ComfyUIModelChecker,
    ComfyUIModelRequirementSchema,
    ComfyUINodeChecker,
    ComfyUINodeRequirementSchema,
)
from src.plugin_api.presets import RequirementChecker, RequirementContext

BASE = "http://127.0.0.1:8188"


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    """Every test gets a cold cache - the module-level `_object_info_cache`/
    `_model_list_cache` are shared per-process singletons."""
    req_mod._object_info_cache._entries.clear()
    req_mod._model_list_cache._entries.clear()
    yield


@dataclass
class _FakeConfig:
    base_url: str

    def get_base_url(self) -> str:
        return self.base_url


@dataclass
class _FakeBackend:
    id: str
    engine: str
    config: Any


def _ctx(backend: Optional[_FakeBackend]) -> RequirementContext:
    return RequirementContext(
        models=None, gpu_available=False, gpu_total_vram_gb=None, backend=backend, platform="linux",
    )


COMFY_BACKEND = _FakeBackend(id="comfy-1", engine="comfyui", config=_FakeConfig(BASE))


class FakeResponse:
    def __init__(self, payload=None, raise_exc=None):
        self._payload = payload
        self._raise_exc = raise_exc

    async def json(self):
        return self._payload

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    def __init__(self, routes: dict):
        self.routes = routes
        self.requested_urls = []

    def get(self, url, *args, **kwargs):
        self.requested_urls.append(url)
        if url not in self.routes:
            raise AssertionError(f"Unexpected URL requested: {url}")
        route = self.routes[url]
        return route() if callable(route) else route

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def patch_session(routes: dict):
    session = FakeSession(routes)
    return patch("aiohttp.ClientSession", return_value=session), session


class TestComfyUINodeChecker:
    @pytest.mark.asyncio
    async def test_ok_when_class_type_is_in_object_info(self):
        routes = {f"{BASE}/object_info": FakeResponse({"KSampler": {}, "FaceDetailer": {}})}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUINodeChecker().check(
                {"type": "comfyui_node", "class_type": "FaceDetailer"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_missing_when_class_type_absent(self):
        routes = {f"{BASE}/object_info": FakeResponse({"KSampler": {}})}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUINodeChecker().check(
                {"type": "comfyui_node", "class_type": "FaceDetailer"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "missing"
        assert "FaceDetailer" in result.hint
        assert result.action.kind == "open_backends"

    @pytest.mark.asyncio
    async def test_unknown_when_no_backend_configured(self):
        result = await ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "X"}, _ctx(None))
        assert result.status == "unknown"
        assert "no ComfyUI backend configured" in result.detail
        assert result.action.kind == "open_backends"

    @pytest.mark.asyncio
    async def test_unknown_when_resolved_backend_is_a_different_engine(self):
        native_backend = _FakeBackend(id="n-1", engine="native", config=object())
        result = await ComfyUINodeChecker().check(
            {"type": "comfyui_node", "class_type": "X"}, _ctx(native_backend)
        )
        assert result.status == "unknown"

    @pytest.mark.asyncio
    async def test_unknown_when_backend_unreachable(self):
        def raise_error():
            raise aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))

        patcher, _session = patch_session({f"{BASE}/object_info": raise_error})
        with patcher:
            result = await ComfyUINodeChecker().check(
                {"type": "comfyui_node", "class_type": "X"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "unknown"
        assert result.detail.startswith(f"backend unreachable at {BASE}")

    @pytest.mark.asyncio
    async def test_object_info_is_cached_across_checks(self):
        """Confirms the TTL cache: two entries checked in the same evaluation
        run must not refetch /object_info twice."""
        routes = {f"{BASE}/object_info": FakeResponse({"KSampler": {}})}
        patcher, session = patch_session(routes)
        with patcher:
            await ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "A"}, _ctx(COMFY_BACKEND))
            await ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "B"}, _ctx(COMFY_BACKEND))
        assert session.requested_urls == [f"{BASE}/object_info"]


class TestComfyUIModelChecker:
    @pytest.mark.asyncio
    async def test_ok_on_exact_match(self):
        routes = {f"{BASE}/models/checkpoints": FakeResponse(["sdxlBase_v10.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "checkpoints", "name": "sdxlBase_v10.safetensors"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_ok_when_server_lists_a_subfolder_but_requirement_omits_it(self):
        routes = {f"{BASE}/models/loras": FakeResponse(["style/foo.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "loras", "name": "foo.safetensors"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_ok_when_requirement_names_a_subfolder_but_server_omits_it(self):
        routes = {f"{BASE}/models/loras": FakeResponse(["foo.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "loras", "name": "style/foo.safetensors"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_missing_when_not_present(self):
        routes = {f"{BASE}/models/checkpoints": FakeResponse(["other.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "checkpoints", "name": "missing.safetensors"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "missing"
        assert "missing.safetensors" in result.hint
        assert result.action.kind == "open_downloader"
        assert result.action.payload == {"query": "missing.safetensors"}

    @pytest.mark.asyncio
    async def test_unknown_when_backend_unreachable(self):
        def raise_error():
            raise aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))

        patcher, _session = patch_session({f"{BASE}/models/vae": raise_error})
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "vae", "name": "x.safetensors"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "unknown"
        assert result.detail.startswith(f"backend unreachable at {BASE}")

    @pytest.mark.asyncio
    async def test_unknown_when_no_backend_configured(self):
        result = await ComfyUIModelChecker().check(
            {"type": "comfyui_model", "folder": "vae", "name": "x.safetensors"}, _ctx(None)
        )
        assert result.status == "unknown"


class TestManifestRegistration:
    """The plugin's own `manifest.yml` declares both checkers with the
    module reference the platform loader expects, and both checker classes
    satisfy `src.plugin_api.presets.RequirementChecker` (a runtime-checkable
    Protocol) - this is what the platform loader instantiates and calls
    once the plugin is enabled (src/platform/plugins/registry.py's
    `_register_plugin_requirement_checkers`, not exercised directly here:
    that machinery lives in src.platform, off limits to a plugin's own tests
    - see tests/architecture/test_layering.py rule 6)."""

    def test_manifest_declares_both_checkers(self):
        manifest_path = req_mod.__spec__.origin.rsplit("backend/requirements.py", 1)[0] + "manifest.yml"
        manifest = yaml.safe_load(open(manifest_path))
        entries = {e["type"]: e["backend"] for e in manifest["requirement_checkers"]}
        assert entries == {
            "comfyui_node": "backend.requirements:ComfyUINodeChecker",
            "comfyui_model": "backend.requirements:ComfyUIModelChecker",
        }

    def test_checkers_satisfy_the_requirement_checker_protocol(self):
        assert isinstance(ComfyUINodeChecker(), RequirementChecker)
        assert isinstance(ComfyUIModelChecker(), RequirementChecker)

    def test_checker_schemas_validate_their_own_manifest_examples(self):
        ComfyUINodeRequirementSchema.model_validate({"type": "comfyui_node", "class_type": "FaceDetailer"})
        ComfyUIModelRequirementSchema.model_validate(
            {"type": "comfyui_model", "folder": "loras", "name": "style.safetensors"}
        )
