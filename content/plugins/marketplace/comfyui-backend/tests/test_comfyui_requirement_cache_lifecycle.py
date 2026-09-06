"""`_SingleFlightTTLCache.get_or_fetch` awaited the shared in-flight fetch
directly in the creating caller and cleaned it up from that caller's own
`finally` - `asyncio.Task.cancel()` cancels whatever inner future/task the
cancelled coroutine is currently suspended on, so cancelling (or timing
out) ONE consumer of a shared fetch transitively cancelled the fetch every
OTHER concurrent consumer for the same key was also relying on, and cache
population/cleanup happened only if that particular caller's own `await`
actually completed. This suite drives the real `ComfyUINodeChecker`/
`ComfyUIModelChecker` (never the cache class directly) against a fake HTTP
response gated by an `asyncio.Event`, so a test can precisely control the
timing of arrivals/departures around one in-flight fetch - no live server.
"""

import asyncio
import gc
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import patch

import pytest

from backend import requirements as req_mod
from backend.requirements import ComfyUIModelChecker, ComfyUINodeChecker
from src.plugin_api.presets import RequirementContext

BASE = "http://127.0.0.1:8188"


class GatedResponse:
    """A fake `aiohttp` response whose body only becomes available once
    `gate` is set - lets a test pause an in-flight fetch at a chosen point
    and orchestrate exactly which callers arrive/depart around it."""

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


class FailingGatedResponse:
    """Same, but the body never arrives - the fetch fails once released."""

    def __init__(self, gate: asyncio.Event, exc: BaseException):
        self._gate = gate
        self._exc = exc

    async def json(self):
        await self._gate.wait()
        raise self._exc

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


def _ctx(backend: Optional[_FakeBackend]) -> RequirementContext:
    return RequirementContext(models=None, gpu_available=False, gpu_total_vram_gb=None, backend=backend, platform="linux")


COMFY_BACKEND = _FakeBackend(id="comfy-1", engine="comfyui", config=_FakeConfig(BASE))

# Generous relative to real scheduling but still fast in wall-clock terms -
# lets task_a/b/c actually progress to their gated await between
# orchestration steps without depending on exact event-loop tick counts.
_STEP = 0.02


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    req_mod._object_info_cache._ready.clear()
    req_mod._object_info_cache._inflight.clear()
    req_mod._model_list_cache._ready.clear()
    req_mod._model_list_cache._inflight.clear()
    yield


class TestCancellingOneWaiterNeverCancelsTheSharedFetch:
    @pytest.mark.asyncio
    async def test_a_cancelled_b_and_a_late_joining_c_both_get_the_correct_verdict(self):
        gate = asyncio.Event()
        routes = {f"{BASE}/object_info": GatedResponse({"KSampler": {}}, gate)}
        patcher, session = patch_session(routes)

        with patcher:
            task_a = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)
            assert session.requested_urls == [f"{BASE}/object_info"]

            task_b = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)

            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a

            task_c = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)

            gate.set()
            result_b = await task_b
            result_c = await task_c

        assert result_b.status == "ok"
        assert result_c.status == "ok"
        # A's cancellation, and C joining after it, never triggered a
        # second fetch - one shared task served every non-cancelled caller.
        assert session.requested_urls == [f"{BASE}/object_info"]

    @pytest.mark.asyncio
    async def test_bite_check_the_same_scenario_for_the_model_checker(self):
        """Same shape, `ComfyUIModelChecker` - proves the fix isn't
        accidentally specific to the node checker's own cache key."""
        gate = asyncio.Event()
        routes = {f"{BASE}/models/loras": GatedResponse(["style.safetensors"], gate)}
        patcher, session = patch_session(routes)
        req = {"type": "comfyui_model", "folder": "loras", "name": "style.safetensors"}

        with patcher:
            task_a = asyncio.create_task(ComfyUIModelChecker().check(req, _ctx(COMFY_BACKEND)))
            await asyncio.sleep(_STEP)
            task_b = asyncio.create_task(ComfyUIModelChecker().check(req, _ctx(COMFY_BACKEND)))
            await asyncio.sleep(_STEP)

            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a

            gate.set()
            result_b = await task_b

        assert result_b.status == "ok"
        assert session.requested_urls == [f"{BASE}/models/loras"]


class TestFetchOutlivesEveryDepartedWaiter:
    @pytest.mark.asyncio
    async def test_the_sole_caller_cancelling_still_lets_the_fetch_complete_and_populate_the_cache(self):
        gate = asyncio.Event()
        routes = {f"{BASE}/object_info": GatedResponse({"KSampler": {}}, gate)}
        patcher, session = patch_session(routes)

        with patcher:
            task_a = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)
            assert session.requested_urls == [f"{BASE}/object_info"]

            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a

            # Nobody is waiting on the fetch now - it must still run to
            # completion on its own and publish its result to the TTL
            # cache, not be torn down along with the caller that started it.
            gate.set()
            await asyncio.sleep(_STEP)

            result_later = await ComfyUINodeChecker().check(
                {"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND)
            )

        assert result_later.status == "ok"
        # Served from the now-populated cache - a second fetch here would
        # mean the original one was torn down along with its sole caller.
        assert session.requested_urls == [f"{BASE}/object_info"]

    @pytest.mark.asyncio
    async def test_a_failing_fetch_with_no_waiters_left_is_never_cached_and_never_logs_unretrieved(self, caplog):
        gate = asyncio.Event()
        routes = {f"{BASE}/object_info": FailingGatedResponse(gate, RuntimeError("boom"))}
        patcher, _session = patch_session(routes)

        with patcher:
            task_a = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)

            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a

            gate.set()
            await asyncio.sleep(_STEP)

        gc.collect()
        await asyncio.sleep(0)
        assert not any("never retrieved" in r.message.lower() for r in caplog.records)
        # A failed fetch must never be cached as if it succeeded: the checker
        # keys the cache by (backend id, address), and nothing may be published
        # under that key or any other.
        assert req_mod._object_info_cache._get_ready(("comfy-1", BASE)) is None
        assert req_mod._object_info_cache._ready == {}

    @pytest.mark.asyncio
    async def test_a_retry_after_a_failed_departed_fetch_starts_a_fresh_one(self):
        gate = asyncio.Event()
        patcher, _session = patch_session({f"{BASE}/object_info": FailingGatedResponse(gate, RuntimeError("boom"))})

        with patcher:
            task_a = asyncio.create_task(
                ComfyUINodeChecker().check({"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND))
            )
            await asyncio.sleep(_STEP)
            task_a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_a
            gate.set()
            await asyncio.sleep(_STEP)

        gate2 = asyncio.Event()
        gate2.set()
        patcher2, session2 = patch_session({f"{BASE}/object_info": GatedResponse({"KSampler": {}}, gate2)})
        with patcher2:
            result = await ComfyUINodeChecker().check(
                {"type": "comfyui_node", "class_type": "KSampler"}, _ctx(COMFY_BACKEND)
            )
        assert result.status == "ok"
        assert session2.requested_urls == [f"{BASE}/object_info"]


class TestDistinctKeysAndSuccessfulSharingControls:
    @pytest.mark.asyncio
    async def test_distinct_folders_never_share_one_fetch(self):
        gate = asyncio.Event()
        gate.set()
        routes = {
            f"{BASE}/models/loras": GatedResponse(["a.safetensors"], gate),
            f"{BASE}/models/vae": GatedResponse(["b.safetensors"], gate),
        }
        patcher, session = patch_session(routes)
        with patcher:
            result_loras = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "loras", "name": "a.safetensors"}, _ctx(COMFY_BACKEND)
            )
            result_vae = await ComfyUIModelChecker().check(
                {"type": "comfyui_model", "folder": "vae", "name": "b.safetensors"}, _ctx(COMFY_BACKEND)
            )
        assert result_loras.status == "ok"
        assert result_vae.status == "ok"
        assert sorted(session.requested_urls) == sorted([f"{BASE}/models/loras", f"{BASE}/models/vae"])

    @pytest.mark.asyncio
    async def test_concurrent_callers_for_the_same_key_still_share_one_fetch(self):
        gate = asyncio.Event()
        routes = {f"{BASE}/object_info": GatedResponse({"KSampler": {}}, gate)}
        patcher, session = patch_session(routes)
        req = {"type": "comfyui_node", "class_type": "KSampler"}

        with patcher:
            tasks = [asyncio.create_task(ComfyUINodeChecker().check(req, _ctx(COMFY_BACKEND))) for _ in range(10)]
            await asyncio.sleep(_STEP)
            gate.set()
            results = await asyncio.gather(*tasks)

        assert all(r.status == "ok" for r in results)
        assert session.requested_urls == [f"{BASE}/object_info"]


class TestJoinedThroughRequirementsPreview:
    """The real production entry point: `api.preview_workflow_requirements`
    runs every inferred entry concurrently via `asyncio.gather` - two
    `comfyui_model` entries naming the same folder must still fetch that
    folder's listing exactly once between them."""

    @pytest.mark.asyncio
    async def test_two_requirement_entries_sharing_a_folder_fetch_it_once(self, monkeypatch):
        from backend import api

        monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: BASE)
        gate = asyncio.Event()
        gate.set()
        routes = {f"{BASE}/models/loras": GatedResponse(["a.safetensors", "b.safetensors"], gate)}
        patcher, session = patch_session(routes)

        # Two LoRA nodes chained together - each contributes its own
        # `comfyui_model` requirement against the SAME `loras` folder.
        workflow = {
            "1": {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": "a.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
                    "model": ["3", 0], "clip": ["3", 1],
                },
            },
            "2": {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": "b.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
                    "model": ["1", 0], "clip": ["1", 1],
                },
            },
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        }
        body = api.AnalyzeWorkflowRequest(workflow=workflow)

        with patcher:
            result = await api.preview_workflow_requirements(body, current_user=None)

        lora_results = [r for r in result["results"] if r["type"] == "comfyui_model" and r["name"] in ("a.safetensors", "b.safetensors")]
        assert {r["name"]: r["status"] for r in lora_results} == {"a.safetensors": "ok", "b.safetensors": "ok"}
        assert session.requested_urls.count(f"{BASE}/models/loras") == 1
