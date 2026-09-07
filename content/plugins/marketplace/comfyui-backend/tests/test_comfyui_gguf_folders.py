"""ComfyUI-GGUF (github.com/city96/ComfyUI-GGUF) registers `unet_gguf`/
`clip_gguf` as separate `folder_paths` keys pointing at the SAME on-disk
directory as `diffusion_models`/`text_encoders`, filtered to the `.gguf`
files ComfyUI's own `supported_pt_extensions` excludes from the ordinary
listing. Before this fix, an installed `.gguf` file was invisible to model
discovery (`comfyui_backend.FOLDER_TO_MODEL_TYPE` didn't recognize either
alias) and always reported "missing" by a `comfyui_model` requirement
(`requirements.ComfyUIModelChecker` queried only the ordinary folder, and
`suggest.py`/`emit.py` recorded that same ordinary folder for a GGUF
loader's own file - see `node_catalog.resolve_model_folder`, the shared
fix). This suite drives discovery, analyze -> preview -> emit/reload, and
the checker through fake HTTP listings shaped like the real upstream
registration - no live server.
"""

from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
import yaml

from backend import requirements as req_mod
from backend.comfyui_backend import ComfyUIBackend
from backend.comfyui_config import ComfyUIBackendConfig
from backend.preset_import.emit import emit_preset
from backend.preset_import.node_catalog import GGUF_FOLDER_ALIAS, resolve_model_folder
from backend.preset_import.parser import parse_api_workflow
from backend.preset_import.schema import ImportForm
from backend.preset_import.suggest import suggest_fields
from backend.requirements import ComfyUIModelChecker
from src.plugin_api.presets import RequirementContext

from ._form_helpers import form_from_roles

BASE = "http://127.0.0.1:8188"


# ----------------------------------------------------------------------
# Fake HTTP plumbing (mirrors test_list_models.py / test_comfyui_requirement_checkers.py)
# ----------------------------------------------------------------------


class FakeResponse:
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
            raise aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=self.status)

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
        return route() if (callable(route) and not isinstance(route, FakeResponse)) else route

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def patch_session(routes: dict):
    session = FakeSession(routes)
    return patch("aiohttp.ClientSession", return_value=session), session


def make_backend() -> ComfyUIBackend:
    config = ComfyUIBackendConfig(id="comfy-1", name="Test ComfyUI", host="127.0.0.1", port=8188, secure=False, timeout_seconds=30)
    return ComfyUIBackend(config)


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    req_mod._object_info_cache._ready.clear()
    req_mod._object_info_cache._inflight.clear()
    req_mod._model_list_cache._ready.clear()
    req_mod._model_list_cache._inflight.clear()
    yield


# ----------------------------------------------------------------------
# 1. Discovery - ComfyUIBackend.list_models()
# ----------------------------------------------------------------------


class TestGGUFFolderDiscovery:
    @pytest.mark.asyncio
    async def test_unet_gguf_and_clip_gguf_merge_into_their_ordinary_model_types(self):
        """Upstream shape: a server with ComfyUI-GGUF installed advertises
        `unet_gguf`/`clip_gguf` alongside the ordinary folders, each
        containing files the ordinary listing omits."""
        backend = make_backend()
        routes = {
            f"{BASE}/models": FakeResponse(["diffusion_models", "unet_gguf", "text_encoders", "clip_gguf"]),
            f"{BASE}/experiment/models/diffusion_models": FakeResponse(
                [{"name": "flux1-dev.safetensors", "pathIndex": 0, "size": 100}]
            ),
            f"{BASE}/experiment/models/unet_gguf": FakeResponse(
                [{"name": "flux1-dev-Q4_K_S.gguf", "pathIndex": 0, "size": 50}]
            ),
            f"{BASE}/experiment/models/text_encoders": FakeResponse(
                [{"name": "clip_l.safetensors", "pathIndex": 0, "size": 10}]
            ),
            f"{BASE}/experiment/models/clip_gguf": FakeResponse(
                [{"name": "t5xxl-Q8_0.gguf", "pathIndex": 0, "size": 20}]
            ),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            results = await backend.list_models()

        by_filename = {r.filename: r for r in results}
        assert set(by_filename) == {
            "flux1-dev.safetensors", "flux1-dev-Q4_K_S.gguf", "clip_l.safetensors", "t5xxl-Q8_0.gguf",
        }
        assert by_filename["flux1-dev-Q4_K_S.gguf"].model_type == "diffusion_model"
        assert by_filename["t5xxl-Q8_0.gguf"].model_type == "text_encoder"
        # Merged with their ordinary counterpart, not a separate type.
        assert by_filename["flux1-dev.safetensors"].model_type == "diffusion_model"
        assert by_filename["clip_l.safetensors"].model_type == "text_encoder"

    @pytest.mark.asyncio
    async def test_bite_check_without_the_alias_map_gguf_files_are_invisible(self):
        """Confirms the fixture above actually exercises the alias map, not
        something already true: the GGUF-only folders vanish entirely if
        `FOLDER_TO_MODEL_TYPE` doesn't know them."""
        from backend import comfyui_backend as comfyui_backend_mod

        stale_map = {k: v for k, v in comfyui_backend_mod.FOLDER_TO_MODEL_TYPE.items() if k not in ("unet_gguf", "clip_gguf")}
        backend = make_backend()
        routes = {f"{BASE}/models": FakeResponse(["unet_gguf", "clip_gguf"])}
        patcher, session = patch_session(routes)
        with patch.object(comfyui_backend_mod, "FOLDER_TO_MODEL_TYPE", stale_map):
            with patcher:
                results = await backend.list_models()
        assert results == []
        assert session.requested_urls == [f"{BASE}/models"]  # neither folder was ever queried

    @pytest.mark.asyncio
    async def test_a_backend_without_comfyui_gguf_installed_behaves_exactly_as_before(self):
        """No `unet_gguf`/`clip_gguf` in the live folder list at all (the
        custom node isn't installed) - the intersection logic already skips
        anything not on the server, so this needs no special handling."""
        backend = make_backend()
        routes = {
            f"{BASE}/models": FakeResponse(["diffusion_models", "text_encoders"]),
            f"{BASE}/experiment/models/diffusion_models": FakeResponse(
                [{"name": "flux1-dev.safetensors", "pathIndex": 0, "size": 100}]
            ),
            f"{BASE}/experiment/models/text_encoders": FakeResponse(
                [{"name": "clip_l.safetensors", "pathIndex": 0, "size": 10}]
            ),
        }
        patcher, session = patch_session(routes)
        with patcher:
            results = await backend.list_models()

        assert {r.filename for r in results} == {"flux1-dev.safetensors", "clip_l.safetensors"}
        assert not any("gguf" in u for u in session.requested_urls)

    @pytest.mark.asyncio
    async def test_no_duplicate_entries_between_ordinary_and_gguf_alias_folders(self):
        backend = make_backend()
        routes = {
            f"{BASE}/models": FakeResponse(["diffusion_models", "unet_gguf"]),
            f"{BASE}/experiment/models/diffusion_models": FakeResponse(
                [{"name": "flux1-dev.safetensors", "pathIndex": 0, "size": 100}]
            ),
            f"{BASE}/experiment/models/unet_gguf": FakeResponse(
                [{"name": "flux1-dev-Q4_K_S.gguf", "pathIndex": 0, "size": 50}]
            ),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            results = await backend.list_models()
        # Two genuinely different files, not deduplicated against each other.
        assert len(results) == 2


# ----------------------------------------------------------------------
# 2. resolve_model_folder - the shared rule
# ----------------------------------------------------------------------


class TestResolveModelFolderDirectly:
    def test_gguf_filename_redirects_to_its_alias(self):
        assert resolve_model_folder("diffusion_models", "flux1-dev-Q4_K_S.gguf") == "unet_gguf"
        assert resolve_model_folder("text_encoders", "t5xxl-Q8_0.gguf") == "clip_gguf"

    def test_ordinary_filename_is_unaffected(self):
        assert resolve_model_folder("diffusion_models", "flux1-dev.safetensors") == "diffusion_models"
        assert resolve_model_folder("text_encoders", "clip_l.safetensors") == "text_encoders"

    def test_a_folder_with_no_known_alias_is_unaffected_even_for_a_gguf_name(self):
        assert resolve_model_folder("checkpoints", "model.gguf") == "checkpoints"

    def test_none_folder_or_non_string_filename_pass_through_safely(self):
        assert resolve_model_folder(None, "x.gguf") is None
        assert resolve_model_folder("diffusion_models", None) == "diffusion_models"
        assert resolve_model_folder("diffusion_models", 123) == "diffusion_models"


# ----------------------------------------------------------------------
# 3. Analyze -> requirements preview -> emit/reload
# ----------------------------------------------------------------------


def _preview_entries(workflow_dict, object_info=None):
    from backend.preset_import.emit import _infer_requirements

    workflow = parse_api_workflow(workflow_dict)
    analysis = suggest_fields(workflow, object_info=object_info)
    return _infer_requirements(workflow, object_info=object_info, candidates=analysis.candidates)


class TestCatalogedUnetLoaderGGUF:
    """UnetLoaderGGUF is catalogued (node_catalog.yml) with `folder:
    diffusion_models` - unconditionally wrong for this loader (it only ever
    loads `.gguf` files), fixed by `_infer_requirements`'s catalog loop
    routing every model-file folder through `resolve_model_folder`."""

    _WORKFLOW = {
        "1": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }

    def test_requirement_uses_the_gguf_alias_folder(self):
        entries = _preview_entries(self._WORKFLOW)
        model_entries = [e for e in entries if e["type"] == "comfyui_model"]
        assert model_entries == [{"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"}]

    def test_bite_check_without_the_fix_the_requirement_names_the_ordinary_folder(self):
        entries = _preview_entries(self._WORKFLOW)
        model_entries = [e for e in entries if e["type"] == "comfyui_model"]
        assert model_entries[0]["folder"] != "diffusion_models"  # would be, pre-fix

    def test_ordinary_unetloader_is_unaffected(self):
        """Regression: the plain (non-GGUF) UNETLoader keeps its ordinary
        folder for an ordinary file - this fix must not touch it."""
        workflow = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "flux1-dev.safetensors"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        entries = _preview_entries(workflow)
        model_entries = [e for e in entries if e["type"] == "comfyui_model"]
        assert model_entries == [{"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev.safetensors"}]

    def test_emit_and_reload_both_keep_the_gguf_alias_folder(self, tmp_path):
        workflow = parse_api_workflow(self._WORKFLOW)
        analysis = suggest_fields(workflow)
        # No model field: a picker-driven loader input is not a requirement.
        form = form_from_roles(analysis, set())
        result = emit_preset(
            workflow, form, [], model_family="GGUFUnetTest", variant="imported",
            display_name="GGUF Unet Test", dest_root=tmp_path,
        )
        preset_yml = yaml.safe_load((result.preset_dir / "preset.yml").read_text())
        assert {"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"} in preset_yml["requirements"]

        reloaded = emit_preset(
            workflow, form, [], model_family="GGUFUnetTest", variant="imported",
            display_name="GGUF Unet Test", dest_root=tmp_path, overwrite=True, preset_id=result.preset_id,
        )
        reloaded_yml = yaml.safe_load((reloaded.preset_dir / "preset.yml").read_text())
        assert {"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"} in reloaded_yml["requirements"]


class TestNoncatalogGGUFLoaders:
    """UnetLoaderGGUFAdvanced and the GGUF CLIP loaders aren't catalogued -
    their model-file inputs are recognized by name alone
    (node_catalog.MODEL_FILE_BY_INPUT_NAME) through suggest.py's
    object_info enrichment, same as any other noncatalog loader."""

    def test_unet_loader_gguf_advanced_infers_the_alias_folder(self):
        workflow = {
            "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        object_info = {
            "UnetLoaderGGUFAdvanced": {
                "input": {"required": {"unet_name": [["flux1-dev-Q4_K_S.gguf", "flux1-dev-Q8_0.gguf"]]}},
                "output": ["MODEL"],
            }
        }
        parsed = parse_api_workflow(workflow)
        analysis = suggest_fields(parsed, object_info=object_info)
        candidate = next(c for c in analysis.candidates if c.input_name == "unet_name")
        assert candidate.suggested_field_type == "model"
        assert candidate.suggested_folder == "unet_gguf"

        entries = _preview_entries(workflow, object_info=object_info)
        model_entries = [e for e in entries if e["type"] == "comfyui_model"]
        assert model_entries == [{"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"}]

    def test_mixed_ordinary_and_gguf_clip_selections_resolve_to_different_folders(self):
        """DualCLIPLoaderGGUF's two clip_name inputs can each independently
        hold an ordinary encoder or a GGUF one - the mixed case must not
        send both files to the same alias."""
        workflow = {
            "1": {
                "class_type": "DualCLIPLoaderGGUF",
                "inputs": {"clip_name1": "clip_l.safetensors", "clip_name2": "t5xxl-Q8_0.gguf"},
            },
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        object_info = {
            "DualCLIPLoaderGGUF": {
                "input": {
                    "required": {
                        "clip_name1": [["clip_l.safetensors", "t5xxl-Q8_0.gguf"]],
                        "clip_name2": [["clip_l.safetensors", "t5xxl-Q8_0.gguf"]],
                    }
                },
                "output": ["CLIP"],
            }
        }
        parsed = parse_api_workflow(workflow)
        analysis = suggest_fields(parsed, object_info=object_info)
        by_input = {c.input_name: c for c in analysis.candidates if c.node_id == "1"}
        assert by_input["clip_name1"].suggested_folder == "text_encoders"
        assert by_input["clip_name2"].suggested_folder == "clip_gguf"

        entries = _preview_entries(workflow, object_info=object_info)
        model_entries = sorted((e["folder"], e["name"]) for e in entries if e["type"] == "comfyui_model")
        assert model_entries == [
            ("clip_gguf", "t5xxl-Q8_0.gguf"),
            ("text_encoders", "clip_l.safetensors"),
        ]

    def test_a_missing_object_info_schema_leaves_the_field_a_plain_literal(self):
        """No live backend at all (offline import) - `unet_name` still
        resolves by name alone (existing behavior, unaffected): a candidate
        is produced, just without the enrichment a reachable server's
        object_info would add. `suggested_folder` still comes from the
        name-based fallback in `_model_file_type_for_combo`, independent of
        object_info - this only characterizes what stays true without it."""
        workflow = {
            "1": {"class_type": "UnetLoaderGGUFAdvanced", "inputs": {"unet_name": "flux1-dev-Q4_K_S.gguf"}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        }
        parsed = parse_api_workflow(workflow)
        analysis = suggest_fields(parsed)  # no object_info
        candidate = next(c for c in analysis.candidates if c.input_name == "unet_name")
        assert candidate.role == "literal"
        assert candidate.suggested_field_type == "textbox"  # no model-file inference without object_info at all


# ----------------------------------------------------------------------
# 4. ComfyUIModelChecker - alias fallback, physical-folder-only text
# ----------------------------------------------------------------------


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
OTHER_BACKEND = _FakeBackend(id="comfy-2", engine="comfyui", config=_FakeConfig("http://127.0.0.1:9999"))


class TestCheckerAliasFallback:
    @pytest.mark.asyncio
    async def test_a_gguf_file_recorded_under_the_ordinary_folder_is_found_via_the_alias(self):
        """A requirement from a preset emitted before this fix (or any other
        source recording the ordinary folder) - the checker itself finds
        the file anyway."""
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev.safetensors"]),
            f"{BASE}/models/unet_gguf": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "ok"
        assert "diffusion_models" in result.detail  # the physical folder, always present in the text
        # Naming the alias here (an "ok" detail, not missing-model guidance)
        # is informative, not misleading - it's how an admin would confirm
        # this went through ComfyUI-GGUF rather than the ordinary listing.

    @pytest.mark.asyncio
    async def test_a_requirement_already_naming_the_alias_is_found_directly(self):
        routes = {f"{BASE}/models/unet_gguf": FakeResponse(["flux1-dev-Q4_K_S.gguf"])}
        patcher, session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "unet_gguf", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "ok"
        assert "diffusion_models" in result.detail  # physical folder named in user-facing text
        assert session.requested_urls == [f"{BASE}/models/unet_gguf"]  # no wasted fallback fetch

    @pytest.mark.asyncio
    async def test_a_genuinely_missing_gguf_file_is_still_reported_missing(self):
        """Present in neither the ordinary nor the alias listing - a real
        gap, never weakened into a false "ok"."""
        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev.safetensors"]),
            f"{BASE}/models/unet_gguf": FakeResponse(["other-Q4_K_S.gguf"]),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "missing"
        assert "diffusion_models" in result.detail
        assert "unet_gguf" not in result.detail
        assert "unet_gguf" not in result.hint

    @pytest.mark.asyncio
    async def test_a_missing_gguf_file_on_a_backend_without_comfyui_gguf_installed_stays_missing_not_unknown(self):
        """The alias folder doesn't exist on this server at all (the custom
        node isn't installed) - the fallback fetch fails, and that failure
        must resolve to "still not found", never surface as its own
        `unknown` result or otherwise mask the real "missing" answer."""
        def alias_404():
            raise aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=404)

        routes = {
            f"{BASE}/models/diffusion_models": FakeResponse(["flux1-dev.safetensors"]),
            f"{BASE}/models/unet_gguf": alias_404,
        }
        patcher, _session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(COMFY_BACKEND),
            )
        assert result.status == "missing"

    @pytest.mark.asyncio
    async def test_ordinary_folder_behavior_is_unaffected_no_alias_fetch_attempted(self):
        routes = {f"{BASE}/models/loras": FakeResponse(["style.safetensors"])}
        patcher, session = patch_session(routes)
        with patcher:
            result = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "loras", "name": "style.safetensors"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "ok"
        assert session.requested_urls == [f"{BASE}/models/loras"]

    @pytest.mark.asyncio
    async def test_a_second_unrelated_backend_without_comfyui_gguf_is_checked_independently(self):
        """Backend A has the file via the ComfyUI-GGUF alias; backend B has
        no ComfyUI-GGUF installed at all (its alias fetch itself fails) -
        each is resolved on its own, and B's absence never leaks into A's
        cached result or vice versa (each keyed by its own backend id)."""
        routes_a = {
            f"{BASE}/models/diffusion_models": FakeResponse([]),
            f"{BASE}/models/unet_gguf": FakeResponse(["flux1-dev-Q4_K_S.gguf"]),
        }

        def other_alias_404():
            raise aiohttp.ClientResponseError(request_info=MagicMock(), history=(), status=404)

        routes_b = {
            "http://127.0.0.1:9999/models/diffusion_models": FakeResponse(["other.safetensors"]),
            "http://127.0.0.1:9999/models/unet_gguf": other_alias_404,
        }

        patcher_a, _session_a = patch_session(routes_a)
        with patcher_a:
            result_a = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(COMFY_BACKEND),
            )
        assert result_a.status == "ok"

        patcher_b, session_b = patch_session(routes_b)
        with patcher_b:
            result_b = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "diffusion_models", "name": "flux1-dev-Q4_K_S.gguf"},
                _ctx(OTHER_BACKEND),
            )
        assert result_b.status == "missing"
        assert "http://127.0.0.1:9999/models/unet_gguf" in session_b.requested_urls
