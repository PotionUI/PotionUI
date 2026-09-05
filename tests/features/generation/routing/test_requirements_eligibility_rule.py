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
from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
from src.features.presets.requirements.context_builder import build_requirement_context_for_backend
from src.features.presets.requirements.contracts import RequirementBackendInfo
from src.features.presets.requirements.evaluator import RequirementsCache
from src.platform.plugins.requirement_checkers import requirement_checker_registry


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


class _FakeGpuMonitor:
    def __init__(self, total_vram_mb: int, available: bool = True):
        self.available = available
        self._total_vram_mb = total_vram_mb

    def get_total_vram(self) -> int:
        return self._total_vram_mb


class TestRequirementsEligibilityWithRealVramMinGb:
    """End-to-end: `vram_min_gb` is backend-scoped, so a real cached
    evaluation of a preset against a local (hard-missing) backend and a
    remote-worker (unknown, no local reading applies) backend of the same
    engine must only narrow routing to the backend that actually satisfies
    it - a host-scoped verdict would have dropped (or kept) both alike."""

    def setup_method(self):
        if not requirement_checker_registry.all():
            register_builtin_requirement_checkers(requirement_checker_registry)

    @pytest.mark.asyncio
    async def test_hard_missing_local_backend_is_dropped_remote_unknown_is_kept(self):
        preset = Mock(id="native-preset", requirements=[{"type": "vram_min_gb", "gb": 16}])
        gpu_monitor = _FakeGpuMonitor(total_vram_mb=8 * 1024)  # 8 GB - below the 16 GB floor
        local_info = RequirementBackendInfo(id="native-local", engine="native", driver="native")
        remote_info = RequirementBackendInfo(id="native-remote-1", engine="native", driver="native.remote")
        host_ctx = build_requirement_context_for_backend(preset, None, gpu_monitor, None)
        backend_ctxs = {
            "native-local": build_requirement_context_for_backend(preset, None, gpu_monitor, local_info),
            "native-remote-1": build_requirement_context_for_backend(preset, None, gpu_monitor, remote_info),
        }
        cache = RequirementsCache()
        await cache.get_or_evaluate_for_backends(requirement_checker_registry, preset, host_ctx, backend_ctxs)

        candidates = _candidates("native-local", "native-remote-1")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="native", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        by_id = {c.backend_id: c for c in result}
        assert by_id["native-local"].dropped
        assert "16 GB" in by_id["native-local"].reasons[-1]
        assert not by_id["native-remote-1"].dropped
        assert by_id["native-remote-1"].reasons[-1] == "requirements satisfied"
