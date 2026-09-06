"""`ComfyUIModelChecker.check` must distinguish an explicitly absent listing
(`GET /models/{folder}` 404s - the server definitively has no such folder,
the ordinary shape of an optional ComfyUI-GGUF alias on a server without
that custom node) from an indeterminate fetch failure (timeout, connection
error, a non-404 HTTP error - the server did not actually answer). Before
this fix:

- A freshly emitted alias-first requirement (`folder: unet_gguf`) checked
  against a server that simply doesn't have ComfyUI-GGUF installed (alias
  404, ordinary folder perfectly reachable) was reported `unknown` -
  "backend unreachable" - as soon as the *primary* listing 404'd, never
  reaching the counterpart lookup at all.
- The *fallback* lookup swallowed every failure (including a genuine
  timeout/5xx, not just a 404) into an empty listing, so an
  ordinary-folder requirement whose alias lookup failed indeterminately
  could be reported definitively `missing` without that listing ever
  actually being obtained.

See `requirements._ListingOutcome`/`_fetch_listing`, the shared fix.
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
import yaml

from backend import api
from backend.requirements import ComfyUIModelChecker
from src.plugin_api.presets import RequirementContext

BASE = "http://127.0.0.1:8188"


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


def _http_404():
    raise aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=404)


def _http_500():
    raise aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=500)


def _timeout():
    raise aiohttp.ServerTimeoutError("timed out")


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


def _ctx(backend) -> RequirementContext:
    return RequirementContext(models=None, gpu_available=False, gpu_total_vram_gb=None, backend=backend, platform="linux")


COMFY_BACKEND = _FakeBackend(id="comfy-1", engine="comfyui", config=_FakeConfig(BASE))


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    from backend import requirements as req_mod

    req_mod._object_info_cache._ready.clear()
    req_mod._object_info_cache._inflight.clear()
    req_mod._model_list_cache._ready.clear()
    req_mod._model_list_cache._inflight.clear()
    yield


# ----------------------------------------------------------------------
# Alias-first requirement (folder: unet_gguf) - what emit.py now records
# directly for a known `.gguf` file (see node_catalog.resolve_model_folder).
# ----------------------------------------------------------------------


class TestAliasFirstRequirementOnAServerMissingComfyUIGGUF:
    """`folder: unet_gguf`, primary listing 404s (no ComfyUI-GGUF here) -
    this must fall through to the physical counterpart, never report
    `unknown` for a perfectly reachable server that simply lacks an
    optional folder."""

    REQUIREMENT = {"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"}

    @pytest.mark.asyncio
    async def test_alias_404_ordinary_reachable_and_has_it_is_ok_not_unknown(self):
        routes = {
            f"{BASE}/models/unet_gguf": _http_404,
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_alias_404_ordinary_reachable_but_missing_is_missing_not_unknown(self):
        routes = {
            f"{BASE}/models/unet_gguf": _http_404,
            f"{BASE}/models/diffusion_models": FakeResponse(["other.safetensors"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "missing"
        assert "diffusion_models" in result.hint

    @pytest.mark.asyncio
    async def test_alias_timeout_ordinary_reachable_and_has_it_is_still_ok(self):
        """The primary listing being indeterminate doesn't matter once the
        counterpart definitively answers "present"."""
        routes = {
            f"{BASE}/models/unet_gguf": _timeout,
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_alias_timeout_ordinary_reachable_but_missing_is_unknown_not_missing(self):
        """The primary (alias) fetch never actually completed - even
        though the counterpart doesn't have it, we can't rule out the
        primary folder actually having it, so this must not be a confident
        `missing`."""
        routes = {
            f"{BASE}/models/unet_gguf": _timeout,
            f"{BASE}/models/diffusion_models": FakeResponse(["other.safetensors"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "unknown"

    @pytest.mark.asyncio
    async def test_alias_5xx_behaves_the_same_as_a_timeout(self):
        routes = {
            f"{BASE}/models/unet_gguf": _http_500,
            f"{BASE}/models/diffusion_models": FakeResponse(["other.safetensors"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "unknown"

    @pytest.mark.asyncio
    async def test_bite_check_a_genuinely_unreachable_server_is_still_unknown(self):
        """Both listings indeterminate (a truly dead server, not just
        missing ComfyUI-GGUF) - still `unknown`, never `missing`."""
        def connection_refused():
            raise aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))

        routes = {f"{BASE}/models/unet_gguf": connection_refused, f"{BASE}/models/diffusion_models": connection_refused}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "unknown"


# ----------------------------------------------------------------------
# Ordinary-folder legacy requirement (folder: diffusion_models) - what a
# preset emitted before the GGUF-alias fix still records.
# ----------------------------------------------------------------------


class TestOrdinaryFolderLegacyRequirement:
    REQUIREMENT = {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"}

    @pytest.mark.asyncio
    async def test_ordinary_missing_alias_404_no_comfyui_gguf_is_missing(self):
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev.safetensors"]),
            f"{BASE}/models/unet_gguf": _http_404,
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_ordinary_missing_alias_timeout_is_unknown_not_missing(self):
        """The core bug this rework fixes: a fallback fetch failure must
        never be silently treated as "not present there either"."""
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev.safetensors"]),
            f"{BASE}/models/unet_gguf": _timeout,
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "unknown"

    @pytest.mark.asyncio
    async def test_bite_check_ordinary_folder_present_short_circuits_before_any_fallback(self):
        routes = {f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev-Q4_K_S.gguf"])}
        patcher, session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQUIREMENT, _ctx(COMFY_BACKEND))
        assert result.status == "ok"
        assert session.requested_urls == [f"{BASE}/models/diffusion_models"]

    @pytest.mark.asyncio
    async def test_a_folder_with_no_gguf_counterpart_at_all_is_unaffected(self):
        """No alias exists for "loras" either way - an indeterminate
        primary failure is still a plain `unknown`, exactly as before this
        rework, with no fallback fetch attempted."""
        def connection_refused():
            raise aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))

        routes = {f"{BASE}/models/loras": connection_refused}
        patcher, session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "loras", "name": "x.safetensors"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "unknown"
        assert session.requested_urls == [f"{BASE}/models/loras"]


# ----------------------------------------------------------------------
# Joined fixture: the real API path (analyze -> preview -> import ->
# reload) produces this exact alias-first requirement, then feeds it to
# the checker under the fixed logic above.
# ----------------------------------------------------------------------


_WORKFLOW = {
    "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
    "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}
_OBJECT_INFO = {
    "UnetLoaderGGUFAdvanced": {
        "input": {"required": {"unet_name": [["flux1-dev-Q4_K_S.gguf", "flux1-dev-Q8_0.gguf"]]}},
        "output": ["MODEL"],
    }
}


@pytest.fixture()
def _imported_root(tmp_path, monkeypatch):
    root = tmp_path / "presets"
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured")))
    return root


def _mock_object_info(monkeypatch):
    async def _fake_fetch_object_info(backend_id, base_url):
        return _OBJECT_INFO

    monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch_object_info)


class TestJoinedAnalyzePreviewImportReloadThenChecker:
    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_the_alias_folder_survives_the_whole_real_api_path_and_the_checker_finds_it(
        self, monkeypatch, _imported_root
    ):
        _mock_object_info(monkeypatch)
        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: BASE)

        analyze_result = await api.analyze_workflow(api.AnalyzeWorkflowRequest(workflow=_WORKFLOW), current_user=None)
        candidate = next(c for c in analyze_result["candidates"] if c["input_name"] == "unet_name")
        assert candidate["suggested_folder"] == "unet_gguf"

        # The preview runs the real checker: serve its listings from the same
        # fake server as the final checker step, never the network.
        preview_patcher, _preview_session = patch_session({
            f"{BASE}/models/unet_gguf": _http_404,
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        })
        with preview_patcher:
            preview_result = await api.preview_workflow_requirements(
                api.AnalyzeWorkflowRequest(workflow=_WORKFLOW), current_user=None
            )
        model_results = [r for r in preview_result["results"] if r["type"] == "comfyui_model"]
        assert [r["name"] for r in model_results] == ["flux1-dev-Q4_K_S.gguf"]

        form = {
            "tabs": [
                {
                    "id": "generation",
                    "label": "Generation",
                    "items": [
                        {
                            "kind": "field",
                            "field_name": "unet",
                            "field_type": "model",
                            "label": "UNET",
                            "config": {"model_type": "diffusion_model"},
                            "mappings": [{"node_id": "1", "input_name": "unet_name", "transform": "strip_model_prefix"}],
                        }
                    ],
                }
            ]
        }
        create_response = await api.import_workflow(
            api.ImportWorkflowRequest(
                workflow=_WORKFLOW, form=form, history=[], model_family="JoinedGGUFTest",
                variant="imported", display_name="Joined GGUF Test",
                schema_fingerprint=analyze_result["schema_fingerprint"],
                schema_object_info_used=analyze_result["object_info_used"],
            ),
            current_user=None,
        )
        from pathlib import Path

        preset_yml = yaml.safe_load((Path(create_response["path"]) / "preset.yml").read_text())
        model_requirements = [r for r in preset_yml["requirements"] if r["type"] == "comfyui_model"]
        assert model_requirements == [{"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"}]

        reload_response = await api.reload_imported_preset(create_response["preset_id"], current_user=None)
        reloaded_yml = yaml.safe_load((Path(reload_response["path"]) / "preset.yml").read_text())
        reloaded_model_requirements = [r for r in reloaded_yml["requirements"] if r["type"] == "comfyui_model"]
        assert reloaded_model_requirements == [
            {"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"}
        ]

        # Feed the requirement the real API path actually produced into the
        # checker, on a server without ComfyUI-GGUF installed (alias 404).
        requirement = reloaded_model_requirements[0]
        routes = {
            f"{BASE}/models/unet_gguf": _http_404,
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(requirement, _ctx(COMFY_BACKEND))
        assert result.status == "ok"
