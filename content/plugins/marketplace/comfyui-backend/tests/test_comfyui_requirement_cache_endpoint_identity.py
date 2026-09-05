"""`_object_info_cache`/`_model_list_cache` were keyed by `backend_id`
(`(backend_id, folder)` for the model listing) with `base_url` living only
inside the fetch closure, never the key. This plugin's own
`_CONFIGURED_BACKEND_ID` (`backend/api.py`) is a FIXED string standing in
for "whatever the plugin's currently configured ComfyUI address resolves
to" - so the same id can legitimately mean a different server from one call
to the next (an admin edits host/port between them). Without the address in
the key: a caller for the new address could be served a stale listing
fetched from the old one, or could join an in-flight fetch that's still
talking to the old address and get its answer instead of its own. `base_url`
now joins both cache keys, so a changed address is simply a different cache
entry - fetched fresh on its own, no global cache flush needed, and an old
fetch's late completion can never satisfy a caller for the new address.
"""

import asyncio
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from backend import requirements as req_mod
from backend.requirements import ComfyUIModelChecker, ComfyUINodeChecker
from src.plugin_api.presets import RequirementContext

from ._form_helpers import raw_form_dict_for_roles

BASE_A = "http://127.0.0.1:8188"
BASE_B = "http://127.0.0.1:9999"
_STEP = 0.02


class FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        return self._payload

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class GatedResponse:
    def __init__(self, payload: Any, gate: asyncio.Event):
        self._payload = payload
        self._gate = gate

    async def json(self):
        await self._gate.wait()
        return self._payload

    def raise_for_status(self):
        pass

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
        return self.routes[url]

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


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    req_mod._object_info_cache._ready.clear()
    req_mod._object_info_cache._inflight.clear()
    req_mod._model_list_cache._ready.clear()
    req_mod._model_list_cache._inflight.clear()
    yield


class TestWarmCacheAddressChangeObjectInfo:
    @pytest.mark.asyncio
    async def test_bite_check_a_changed_address_fetches_fresh_not_the_old_addresss_cached_data(self):
        routes = {
            f"{BASE_A}/object_info": FakeResponse({"NodeA": {}}),
            f"{BASE_B}/object_info": FakeResponse({"NodeB": {}}),
        }
        patcher, session = patch_session(routes)
        with patcher:
            info_a = await req_mod._fetch_object_info("comfy-1", BASE_A)
            info_b = await req_mod._fetch_object_info("comfy-1", BASE_B)

        assert info_a == {"NodeA": {}}
        assert info_b == {"NodeB": {}}
        assert sorted(session.requested_urls) == sorted([f"{BASE_A}/object_info", f"{BASE_B}/object_info"])

    @pytest.mark.asyncio
    async def test_reverting_the_address_back_still_gets_a_correct_answer(self):
        """Not just "new address works" - the ORIGINAL address's own cache
        entry (keyed on its own url) must still be independently valid too,
        never clobbered by the other address's entry sharing the same
        backend_id."""
        routes = {
            f"{BASE_A}/object_info": FakeResponse({"NodeA": {}}),
            f"{BASE_B}/object_info": FakeResponse({"NodeB": {}}),
        }
        patcher, session = patch_session(routes)
        with patcher:
            await req_mod._fetch_object_info("comfy-1", BASE_A)
            await req_mod._fetch_object_info("comfy-1", BASE_B)
            info_a_again = await req_mod._fetch_object_info("comfy-1", BASE_A)

        assert info_a_again == {"NodeA": {}}
        # Third call served from A's own now-warm cache entry - no new fetch.
        assert session.requested_urls.count(f"{BASE_A}/object_info") == 1


class TestWarmCacheAddressChangeModelListing:
    @pytest.mark.asyncio
    async def test_bite_check_a_changed_address_fetches_fresh_model_listing(self):
        routes = {
            f"{BASE_A}/models/loras": FakeResponse(["a.safetensors"]),
            f"{BASE_B}/models/loras": FakeResponse(["b.safetensors"]),
        }
        patcher, session = patch_session(routes)
        with patcher:
            names_a = await req_mod._fetch_model_names("comfy-1", BASE_A, "loras")
            names_b = await req_mod._fetch_model_names("comfy-1", BASE_B, "loras")

        assert names_a == ["a.safetensors"]
        assert names_b == ["b.safetensors"]
        assert sorted(session.requested_urls) == sorted([f"{BASE_A}/models/loras", f"{BASE_B}/models/loras"])


class TestInFlightAddressDoesNotAbsorbAnotherAddress:
    @pytest.mark.asyncio
    async def test_b_fetches_its_own_object_info_while_a_is_still_in_flight(self):
        gate_a = asyncio.Event()
        routes = {
            f"{BASE_A}/object_info": GatedResponse({"NodeA": {}}, gate_a),
            f"{BASE_B}/object_info": FakeResponse({"NodeB": {}}),
        }
        patcher, session = patch_session(routes)

        with patcher:
            task_a = asyncio.create_task(req_mod._fetch_object_info("comfy-1", BASE_A))
            await asyncio.sleep(_STEP)
            assert session.requested_urls == [f"{BASE_A}/object_info"]

            # Same backend_id, a different address, while A is still
            # in-flight - must fetch on its own, never wait on A's gate.
            info_b = await asyncio.wait_for(req_mod._fetch_object_info("comfy-1", BASE_B), timeout=2.0)
            assert info_b == {"NodeB": {}}
            assert f"{BASE_B}/object_info" in session.requested_urls

            gate_a.set()
            info_a = await task_a

        assert info_a == {"NodeA": {}}

    @pytest.mark.asyncio
    async def test_as_late_completion_never_replaces_bs_already_cached_result(self):
        gate_a = asyncio.Event()
        routes = {
            f"{BASE_A}/models/loras": GatedResponse(["a.safetensors"], gate_a),
            f"{BASE_B}/models/loras": FakeResponse(["b.safetensors"]),
        }
        patcher, session = patch_session(routes)

        with patcher:
            task_a = asyncio.create_task(req_mod._fetch_model_names("comfy-1", BASE_A, "loras"))
            await asyncio.sleep(_STEP)

            names_b = await asyncio.wait_for(req_mod._fetch_model_names("comfy-1", BASE_B, "loras"), timeout=2.0)
            assert names_b == ["b.safetensors"]

            gate_a.set()
            names_a = await task_a
            # B's own result, re-read after A's fetch finally landed, is
            # still exactly B's - A's completion did not overwrite it.
            names_b_again = await req_mod._fetch_model_names("comfy-1", BASE_B, "loras")

        assert names_a == ["a.safetensors"]
        assert names_b_again == ["b.safetensors"]
        assert session.requested_urls.count(f"{BASE_B}/models/loras") == 1  # served from B's own cache entry


class TestSingleFlightAndTTLStillWorkPerEndpoint:
    """COMFY-06 isolation preserved: identical `(backend_id, base_url[, folder])`
    still single-flights and still hits its TTL entry."""

    @pytest.mark.asyncio
    async def test_concurrent_callers_for_the_same_backend_and_address_still_share_one_fetch(self):
        gate = asyncio.Event()
        routes = {f"{BASE_A}/object_info": GatedResponse({"KSampler": {}}, gate)}
        patcher, session = patch_session(routes)
        req = {"type": "comfyui_node", "class_type": "KSampler"}
        backend = _FakeBackend(id="comfy-1", engine="comfyui", config=_FakeConfig(BASE_A))

        with patcher:
            tasks = [asyncio.create_task(ComfyUINodeChecker().check(req, _ctx(backend))) for _ in range(5)]
            await asyncio.sleep(_STEP)
            gate.set()
            results = await asyncio.gather(*tasks)

        assert all(r.status == "ok" for r in results)
        assert session.requested_urls == [f"{BASE_A}/object_info"]

    @pytest.mark.asyncio
    async def test_distinct_backend_ids_at_the_same_address_are_still_isolated(self):
        routes = {f"{BASE_A}/models/loras": FakeResponse(["a.safetensors"])}
        patcher, session = patch_session(routes)
        req = {"type": "comfyui_model", "folder": "loras", "name": "a.safetensors"}

        with patcher:
            result_x = await ComfyUIModelChecker().check(req, _ctx(_FakeBackend(id="comfy-x", engine="comfyui", config=_FakeConfig(BASE_A))))
            result_y = await ComfyUIModelChecker().check(req, _ctx(_FakeBackend(id="comfy-y", engine="comfyui", config=_FakeConfig(BASE_A))))

        assert result_x.status == result_y.status == "ok"
        # Each backend_id fetches its own copy even at the same address -
        # unaffected by this fix, a pre-existing (and still correct)
        # per-backend-id isolation.
        assert session.requested_urls.count(f"{BASE_A}/models/loras") == 2

    @pytest.mark.asyncio
    async def test_a_ttl_hit_at_the_same_address_never_refetches(self):
        routes = {f"{BASE_A}/object_info": FakeResponse({"KSampler": {}})}
        patcher, session = patch_session(routes)
        with patcher:
            await req_mod._fetch_object_info("comfy-1", BASE_A)
            await req_mod._fetch_object_info("comfy-1", BASE_A)
        assert session.requested_urls == [f"{BASE_A}/object_info"]


# ----------------------------------------------------------------------
# The real production seam: api._try_object_info / analyze / import, with
# the plugin's own fixed backend identity and a changed source address.
# ----------------------------------------------------------------------


@pytest.fixture()
def _imported_root(tmp_path, monkeypatch):
    from backend import api

    root = tmp_path / "presets"
    monkeypatch.setattr(api, "_IMPORTED_PRESETS_ROOT", root)
    monkeypatch.setattr(api, "get_container", lambda: (_ for _ in ()).throw(AssertionError("no container configured")))
    return root


class TestTryObjectInfoSeamWithAChangedAddress:
    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_try_object_info_reflects_a_changed_address_with_no_global_flush(self, monkeypatch):
        from backend import api

        addresses = iter([BASE_A, BASE_B])
        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: next(addresses))
        routes = {
            f"{BASE_A}/object_info": FakeResponse({"NodeA": {}}),
            f"{BASE_B}/object_info": FakeResponse({"NodeB": {}}),
        }
        patcher, _session = patch_session(routes)
        with patcher:
            info_first = await api._try_object_info()
            info_second = await api._try_object_info()

        assert info_first == {"NodeA": {}}
        assert info_second == {"NodeB": {}}


_DRIFT_WORKFLOW = {
    "1": {"class_type": "MyAllInOneGenerator", "inputs": {"prompt": "a cat wearing a hat"}},
    "2": {"class_type": "MyStringHelper", "inputs": {"text": "static filename suffix"}},
    "3": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
}
_OBJECT_INFO_AT_ADDRESS_A = {
    "MyAllInOneGenerator": {"input": {"required": {"prompt": ["STRING", {"multiline": True}]}}, "output": ["IMAGE"]},
    "MyStringHelper": {"input": {"required": {"text": ["STRING", {}]}}, "output": ["STRING"]},
}
_OBJECT_INFO_AT_ADDRESS_B = {
    **_OBJECT_INFO_AT_ADDRESS_A,
    # The server at this address genuinely classifies the helper node
    # differently (e.g. a different ComfyUI-GGUF/custom-node install).
    "MyStringHelper": {**_OBJECT_INFO_AT_ADDRESS_A["MyStringHelper"], "output": ["IMAGE"]},
}


class TestSchemaDriftReachableAfterAnAddressChange:
    """A backend reconfigured to a new address between analyze and save,
    whose server at that new address genuinely classifies the workflow
    differently, must still reach COMFY-04's existing schema-drift refusal
    - proving the endpoint-identity fix doesn't let a backend_id-keyed
    cache mask a real classification change behind stale, same-id data."""

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_a_genuinely_different_server_at_the_new_address_reaches_the_drift_refusal(self, monkeypatch, _imported_root):
        from backend import api

        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: BASE_A)
        routes = {
            f"{BASE_A}/object_info": FakeResponse(_OBJECT_INFO_AT_ADDRESS_A),
            f"{BASE_B}/object_info": FakeResponse(_OBJECT_INFO_AT_ADDRESS_B),
        }
        patcher, _session = patch_session(routes)

        with patcher:
            analyze_result = await api.analyze_workflow(
                api.AnalyzeWorkflowRequest(workflow=_DRIFT_WORKFLOW), current_user=None
            )
            form = raw_form_dict_for_roles(analyze_result, {"literal"})

            # The backend is reconfigured to a new address before saving.
            monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: BASE_B)

            body = api.ImportWorkflowRequest(
                workflow=_DRIFT_WORKFLOW, form=form, history=[], model_family="EndpointDriftTest",
                variant="imported", display_name="Endpoint Drift Test",
                schema_fingerprint=analyze_result["schema_fingerprint"],
                schema_object_info_used=analyze_result["object_info_used"],
            )
            with pytest.raises(Exception) as exc_info:
                await api.import_workflow(body, current_user=None)

        assert exc_info.value.status_code == 400
        assert "classification" in exc_info.value.detail

    @pytest.mark.uses_object_info_mock
    @pytest.mark.asyncio
    async def test_bite_check_without_endpoint_identity_the_new_address_would_be_masked_by_the_stale_cache(self, monkeypatch, _imported_root):
        """Confirms the mechanism directly: analyzing at A twice in a row
        (same backend_id, same address both times) must NOT drift - a
        sanity control showing the refusal above really comes from the
        address changing, not from re-analyzing at all."""
        from backend import api

        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: BASE_A)
        routes = {f"{BASE_A}/object_info": FakeResponse(_OBJECT_INFO_AT_ADDRESS_A)}
        patcher, _session = patch_session(routes)

        with patcher:
            analyze_result = await api.analyze_workflow(
                api.AnalyzeWorkflowRequest(workflow=_DRIFT_WORKFLOW), current_user=None
            )
            form = raw_form_dict_for_roles(analyze_result, {"literal"})

            body = api.ImportWorkflowRequest(
                workflow=_DRIFT_WORKFLOW, form=form, history=[], model_family="NoDriftSameAddressTest",
                variant="imported", display_name="No Drift Same Address Test",
                schema_fingerprint=analyze_result["schema_fingerprint"],
                schema_object_info_used=analyze_result["object_info_used"],
            )
            response = await api.import_workflow(body, current_user=None)

        assert response["preset_id"]
