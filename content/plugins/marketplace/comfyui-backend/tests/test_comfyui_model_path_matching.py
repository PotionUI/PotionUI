"""`ComfyUIModelChecker`'s `_model_present` fell back to a bare basename
comparison unconditionally, so a requirement recorded as
`sdxl/portrait.safetensors` was satisfied by a listing containing only
`flux/portrait.safetensors` - two different, same-named files in different
collections treated as the same one. `_model_present` now only trusts a
basename match when at least one side genuinely lacks subfolder evidence;
when both the requirement and a candidate carry a subfolder, only an exact
(separator-normalized, case-preserving) path match counts.
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from backend import requirements as req_mod
from backend.preset_import.emit import emit_preset
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.suggest import suggest_fields
from backend.requirements import ComfyUIModelChecker, _model_present
from src.plugin_api.presets import RequirementContext

from ._form_helpers import form_from_roles

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
    req_mod._object_info_cache._ready.clear()
    req_mod._object_info_cache._inflight.clear()
    req_mod._model_list_cache._ready.clear()
    req_mod._model_list_cache._inflight.clear()
    yield


class TestModelPresentDirectly:
    def test_bite_check_different_subfolders_same_basename_are_not_the_same_file(self):
        assert _model_present("sdxl/portrait.safetensors", ["flux/portrait.safetensors"]) is False

    def test_the_exact_subfoldered_path_matches(self):
        assert _model_present("sdxl/portrait.safetensors", ["flux/portrait.safetensors", "sdxl/portrait.safetensors"]) is True

    def test_a_bare_wanted_name_still_matches_a_subfoldered_candidate(self):
        """One side (the requirement) genuinely has no subfolder evidence -
        real, residual ambiguity, basename fallback still applies."""
        assert _model_present("portrait.safetensors", ["sdxl/portrait.safetensors"]) is True

    def test_a_subfoldered_wanted_name_still_matches_a_bare_candidate(self):
        """The other direction - server's own listing has no subfolder for
        this entry at all."""
        assert _model_present("sdxl/portrait.safetensors", ["portrait.safetensors"]) is True

    def test_a_file_absent_from_both_known_subpaths_stays_missing(self):
        assert _model_present("sdxl/portrait.safetensors", ["flux/portrait.safetensors", "other/portrait.safetensors"]) is False

    def test_backslash_separators_normalize_but_spelling_and_case_are_untouched(self):
        assert _model_present("sdxl\\portrait.safetensors", ["sdxl/portrait.safetensors"]) is True
        assert _model_present("SDXL/Portrait.safetensors", ["sdxl/portrait.safetensors"]) is False  # no case folding

    def test_exact_match_short_circuits_before_any_basename_fallback(self):
        assert _model_present("x.safetensors", ["x.safetensors"]) is True

    def test_two_bare_names_with_different_spelling_are_not_the_same_file(self):
        assert _model_present("portrait.safetensors", ["portraits.safetensors"]) is False


class TestCheckerThroughThePathAwareComparison:
    REQ = {"type": "comfyui_model", "folder": "checkpoints", "name": "sdxl/portrait.safetensors"}

    @pytest.mark.asyncio
    async def test_a_same_basename_alternative_under_a_different_subfolder_is_reported_missing(self):
        routes = {f"{BASE}/models/checkpoints": FakeResponse(["flux/portrait.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQ, _ctx(COMFY_BACKEND))
        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_the_exact_authored_subfolder_is_reported_ok(self):
        routes = {f"{BASE}/models/checkpoints": FakeResponse(["flux/portrait.safetensors", "sdxl/portrait.safetensors"])}
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(self.REQ, _ctx(COMFY_BACKEND))
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_the_gguf_alias_fallback_uses_the_same_path_aware_comparison(self):
        """Shared logic - the alias fallback listing must be just as strict
        about a mismatched subfolder as the primary one."""
        req = {"type": "comfyui_model", "folder": "diffusion_models", "name": "sdxl/flux1-Q4.gguf"}
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse([]),
            f"{BASE}/models/unet_gguf": FakeResponse(["other/flux1-Q4.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(req, _ctx(COMFY_BACKEND))
        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_the_gguf_alias_fallback_finds_the_exact_subfoldered_path(self):
        req = {"type": "comfyui_model", "folder": "diffusion_models", "name": "sdxl/flux1-Q4.gguf"}
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse([]),
            f"{BASE}/models/unet_gguf": FakeResponse(["sdxl/flux1-Q4.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(req, _ctx(COMFY_BACKEND))
        assert result.status == "ok"


class TestJoinedImportedNestedFilename:
    """An authored requirement with a nested selection (a subfolder in the
    workflow's own literal value) survives verbatim through analyze/emit -
    then a same-basename alternative in a different subfolder must not be
    reported installed against it, while its own exact path is."""

    def test_the_authored_subfolder_selection_survives_emit(self, tmp_path):
        workflow = parse_api_workflow(
            {
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl/portrait.safetensors"}},
                "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
            }
        )
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})
        result = emit_preset(
            workflow, form, [], model_family="NestedPathTest", variant="imported",
            display_name="Nested Path Test", dest_root=tmp_path,
        )
        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        model_requirements = [r for r in preset_yml["requirements"] if r["type"] == "comfyui_model"]
        assert model_requirements == [{"type": "comfyui_model", "folder": "checkpoints", "name": "sdxl/portrait.safetensors"}]

    @pytest.mark.asyncio
    async def test_the_emitted_requirement_rejects_a_same_basename_alternative_and_accepts_its_own_path(self, tmp_path):
        workflow = parse_api_workflow(
            {
                "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl/portrait.safetensors"}},
                "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
            }
        )
        analysis = suggest_fields(workflow)
        form = form_from_roles(analysis, {"checkpoint"})
        result = emit_preset(
            workflow, form, [], model_family="NestedPathTest2", variant="imported",
            display_name="Nested Path Test 2", dest_root=tmp_path,
        )
        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        requirement = next(r for r in preset_yml["requirements"] if r["type"] == "comfyui_model")

        routes_mismatch = {f"{BASE}/models/checkpoints": FakeResponse(["flux/portrait.safetensors"])}
        patcher, _session = patch_session(routes_mismatch)
        with patcher:
            result_mismatch = await ComfyUIModelChecker().check(requirement, _ctx(COMFY_BACKEND))
        assert result_mismatch.status == "missing"

        # Otherwise the second check would just replay the first fetch's
        # now-cached (and, for this test, deliberately different) listing.
        req_mod._model_list_cache._ready.clear()

        routes_exact = {f"{BASE}/models/checkpoints": FakeResponse(["flux/portrait.safetensors", "sdxl/portrait.safetensors"])}
        patcher2, _session2 = patch_session(routes_exact)
        with patcher2:
            result_exact = await ComfyUIModelChecker().check(requirement, _ctx(COMFY_BACKEND))
        assert result_exact.status == "ok"
