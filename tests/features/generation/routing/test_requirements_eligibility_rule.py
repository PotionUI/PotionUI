"""`rules.RequirementsEligibility` - routing eligibility from a preset's
per-backend requirements verdicts (see docs/presets.md "Requirements"). A
backend with a cached hard (non-optional) `missing` requirement is dropped;
a backend never checked yet ("unknown") is kept and a background refresh is
scheduled via `ctx.schedule_background`; host-scoped misses never narrow
here (evaluated once, so they'd exclude every backend identically).
"""

from unittest.mock import Mock

import pytest

from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import RequirementsEligibility


def _candidates(*backend_ids):
    return [Candidate(backend=Mock(backend_id=bid)) for bid in backend_ids]


def _request(requirements):
    return RoutingRequest(engine="comfyui", preset=Mock(id="p1", requirements=requirements), form_data={})


class TestRequirementsEligibility:
    @pytest.mark.asyncio
    async def test_no_requirements_does_not_narrow(self):
        candidates = _candidates("comfy_a")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=Mock())

        result = await RequirementsEligibility().apply(candidates, _request([]), ctx)

        assert not result[0].dropped

    @pytest.mark.asyncio
    async def test_no_requirements_cache_does_not_narrow(self):
        candidates = _candidates("comfy_a")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=None)
        request = _request([{"type": "comfyui_node", "class_type": "X"}])

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not result[0].dropped

    @pytest.mark.asyncio
    async def test_backend_with_no_hard_miss_is_kept(self):
        candidates = _candidates("comfy_a", "comfy_b")
        cache = Mock()
        cache.peek_backend_missing.side_effect = lambda registry, preset, backend_id: (
            [] if backend_id == "comfy_a" else ["FaceDetailer node"]
        )
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = _request([{"type": "comfyui_node", "class_type": "FaceDetailer"}])

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        by_id = {c.backend_id: c for c in result}
        assert not by_id["comfy_a"].dropped
        assert by_id["comfy_b"].dropped
        assert "FaceDetailer node" in by_id["comfy_b"].reasons[-1]

    @pytest.mark.asyncio
    async def test_uncached_backend_is_kept_as_unknown_and_refresh_is_scheduled(self):
        candidates = _candidates("comfy_a")
        cache = Mock()
        cache.peek_backend_missing.return_value = None  # never evaluated
        scheduled = []
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache, schedule_background=scheduled.append)
        request = _request([{"type": "comfyui_node", "class_type": "X"}])

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not result[0].dropped
        assert "not yet checked" in result[0].reasons[-1]
        assert len(scheduled) == 1
        scheduled[0].close()  # never awaited by design here - avoid the GC warning

    @pytest.mark.asyncio
    async def test_optional_miss_never_drops(self):
        candidates = _candidates("comfy_a")
        cache = Mock()
        cache.peek_backend_missing.return_value = []  # peek already excludes optional misses
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = _request([{"type": "comfyui_node", "class_type": "X", "optional": True}])

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not result[0].dropped

    @pytest.mark.asyncio
    async def test_only_live_candidates_are_checked(self):
        """A candidate already dropped by an earlier rule is left alone -
        this rule must not resurrect or re-annotate it."""
        candidates = _candidates("comfy_a", "comfy_b")
        candidates[1].drop("not the requested backend")
        cache = Mock()
        cache.peek_backend_missing.return_value = []
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = _request([{"type": "comfyui_node", "class_type": "X"}])

        await RequirementsEligibility().apply(candidates, request, ctx)

        cache.peek_backend_missing.assert_called_once()
        assert cache.peek_backend_missing.call_args[0][2] == "comfy_a"
