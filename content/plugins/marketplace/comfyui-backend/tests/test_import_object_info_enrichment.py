"""`/presets/import/analyze` and `.../presets/imported/{id}/source` best-
effort enrich their analysis with a reachable ComfyUI backend's own
`/object_info` (real min/max/step, live combo options - see
`suggest._enrich_with_object_info`), sharing `backend.requirements`'s
single-flight cache with the Requirements-preview step. Neither endpoint
ever fails or blocks meaningfully when no backend is reachable - that's the
plain Export (API) import's default, exercised by every other test in this
suite that calls these endpoints without mocking anything.
"""

import json
from pathlib import Path

import pytest

from backend import api
from backend.requirements import _object_info_cache

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    with open(FIXTURES / name) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _clear_object_info_cache():
    """`backend.requirements._object_info_cache` is a module-level singleton
    (shared with `preview_workflow_requirements` on purpose - see
    `api._try_object_info`'s docstring), so a fetch this test primes would
    otherwise leak into the next one for the same TTL window."""
    _object_info_cache._ready.clear()
    _object_info_cache._inflight.clear()
    yield
    _object_info_cache._ready.clear()
    _object_info_cache._inflight.clear()


@pytest.mark.uses_object_info_mock
@pytest.mark.asyncio
async def test_analyze_uses_a_reachable_backends_object_info_when_available(monkeypatch):
    async def _fake_fetch_object_info(backend_id, base_url):
        return {
            "KSampler": {
                "input": {"required": {"steps": ["INT", {"min": 5, "max": 50, "step": 1}]}},
            }
        }

    monkeypatch.setattr(api, "_fetch_object_info", _fake_fetch_object_info)
    body = api.AnalyzeWorkflowRequest(workflow=_load("sdxl_basic_api.json"))

    result = await api.analyze_workflow(body, current_user=None)

    assert result["object_info_used"] is True
    steps_candidate = next(c for c in result["candidates"] if c["role"] == "steps")
    assert steps_candidate["suggested_config"]["min"] == 5
    assert steps_candidate["suggested_config"]["max"] == 50


@pytest.mark.uses_object_info_mock
@pytest.mark.asyncio
async def test_bite_check_an_unreachable_backend_never_uses_object_info(monkeypatch):
    """Confirms the assertion above is really reading the enrichment path:
    with no reachable ComfyUI server, analyze falls back to no enrichment,
    exactly as before. Points at a deliberately unreachable address rather
    than assuming nothing answers on the plugin's default host/port, which
    this sandbox (unlike CI) cannot guarantee."""
    monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: "http://127.0.0.1:1")
    body = api.AnalyzeWorkflowRequest(workflow=_load("sdxl_basic_api.json"))

    result = await api.analyze_workflow(body, current_user=None)

    assert result["object_info_used"] is False


@pytest.mark.uses_object_info_mock
@pytest.mark.asyncio
async def test_object_info_fetch_failure_does_not_fail_the_analyze_call(monkeypatch):
    async def _boom(backend_id, base_url):
        raise RuntimeError("backend unreachable")

    monkeypatch.setattr(api, "_fetch_object_info", _boom)
    body = api.AnalyzeWorkflowRequest(workflow=_load("sdxl_basic_api.json"))

    result = await api.analyze_workflow(body, current_user=None)

    assert result["object_info_used"] is False


@pytest.mark.asyncio
async def test_conftests_network_isolation_short_circuits_before_touching_the_base_url(monkeypatch):
    """No `@pytest.mark.uses_object_info_mock` here - this test proves the
    autouse fixture in conftest.py (`_isolate_object_info_from_the_network`)
    is really in effect for an ordinary, unmocked test: `_try_object_info`
    must return `None` WITHOUT ever calling `_get_comfyui_base_url` at all,
    not merely because nothing answers on the resulting URL. If this ever
    starts calling the real base-url/fetch path again, some later test in
    this suite could reach a real ComfyUI server."""
    calls = []
    monkeypatch.setattr(api, "_get_comfyui_base_url", lambda: calls.append(1) or "http://127.0.0.1:8188")

    assert await api._try_object_info() is None
    assert calls == []
