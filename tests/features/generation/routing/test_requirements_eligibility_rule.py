"""`rules.RequirementsEligibility` - routing eligibility from a preset's
per-backend requirements verdicts (see docs/presets.md "Requirements"). A
backend with a cached hard (non-optional) `missing` requirement is dropped;
a backend never checked yet ("unknown") is kept and a background refresh is
scheduled via `ctx.schedule_background`; host-scoped misses never narrow
here (evaluated once, so they'd exclude every backend identically).
"""

from unittest.mock import Mock

import pytest

from src.features.backends.base_backend import ExecutionDeviceEvidence
from src.features.generation.routing.contracts import Candidate, RoutingContext, RoutingRequest
from src.features.generation.routing.rules import RequirementsEligibility
from src.features.presets.requirements.builtin import register_builtin_requirement_checkers
from src.features.presets.requirements.context_builder import build_requirement_context_for_backend
from src.features.presets.requirements.contracts import RequirementBackendInfo, RequirementResult
from src.features.presets.requirements.evaluator import RequirementsCache
from src.platform.plugins.requirement_checkers import RequirementCheckerRegistration, requirement_checker_registry


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
    def __init__(self, total_vram_mb: int, available: bool = True, device_index: int = 0):
        self.available = available
        self._total_vram_mb = total_vram_mb
        self.device_index = device_index

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
        local_info = RequirementBackendInfo(
            id="native-local", engine="native", driver="native",
            execution_device=ExecutionDeviceEvidence(kind="this_host_gpu", gpu_index=0),
        )
        remote_info = RequirementBackendInfo(
            id="native-remote-1", engine="native", driver="native.remote",
            execution_device=ExecutionDeviceEvidence(kind="remote"),
        )
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

    @pytest.mark.asyncio
    async def test_unestablished_execution_device_backend_is_kept_not_excluded(self):
        """A comfyui-shaped backend (network `host`, no `execution_device`
        override) always reads `unknown` for `vram_min_gb` regardless of
        this API host's own GPU total - it must never be excluded by
        routing eligibility for a requirement it cannot be evaluated
        against."""
        preset = Mock(id="comfy-preset", requirements=[{"type": "vram_min_gb", "gb": 16}])
        gpu_monitor = _FakeGpuMonitor(total_vram_mb=24 * 1024)  # plenty, but irrelevant here
        comfy_info = RequirementBackendInfo(
            id="comfy-worker", engine="comfyui", driver="comfyui",
            execution_device=ExecutionDeviceEvidence(kind="unestablished"),
        )
        host_ctx = build_requirement_context_for_backend(preset, None, gpu_monitor, None)
        backend_ctxs = {"comfy-worker": build_requirement_context_for_backend(preset, None, gpu_monitor, comfy_info)}
        cache = RequirementsCache()
        await cache.get_or_evaluate_for_backends(requirement_checker_registry, preset, host_ctx, backend_ctxs)

        candidates = _candidates("comfy-worker")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="comfyui", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not result[0].dropped
        assert result[0].reasons[-1] == "requirements satisfied"

    @pytest.mark.asyncio
    async def test_cuda1_candidate_against_an_index0_monitor_is_kept_as_unknown_not_falsely_dropped(self):
        """The reopened bug, at the routing layer: GPU0=24 GiB (this
        process's one `GpuMonitor`), GPU1=8 GiB, 16 GiB requirement - a
        REAL `NativeBackend` resolved to `cuda:1` must never be judged by
        GPU0's reading. Before this rework it would have falsely read "ok"
        (borrowed GPU0's 24 GiB) or, with the sizes reversed, falsely
        "missing" (borrowed GPU0's 8 GiB) - either way wrongly settling
        routing eligibility for hardware this process never actually read."""
        from src.features.backends.backend_config import NativeBackendConfig
        from src.features.backends.native_backend import NativeBackend

        preset = Mock(id="dual-gpu-preset", requirements=[{"type": "vram_min_gb", "gb": 16}])
        gpu_monitor = _FakeGpuMonitor(total_vram_mb=24 * 1024, device_index=0)
        gpu1_backend = NativeBackend(
            backend_config=NativeBackendConfig(id="gpu1-backend", name="GPU 1", device="cuda:1", dtype="float16", gpu_max_vram=0)
        )
        gpu1_info = RequirementBackendInfo(
            id="gpu1-backend", engine="native", driver="native",
            execution_device=gpu1_backend.resolve_execution_device(),
        )
        host_ctx = build_requirement_context_for_backend(preset, None, gpu_monitor, None)
        backend_ctxs = {"gpu1-backend": build_requirement_context_for_backend(preset, None, gpu_monitor, gpu1_info)}
        cache = RequirementsCache()
        await cache.get_or_evaluate_for_backends(requirement_checker_registry, preset, host_ctx, backend_ctxs)

        candidates = _candidates("gpu1-backend")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="native", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not result[0].dropped
        assert result[0].reasons[-1] == "requirements satisfied"


class _AlwaysMissingHostChecker:
    """A "host"-scoped checker (the default) whose verdict is always
    "missing" - simulates a missing host-wide binary/package."""

    type = "routing_test_missing_host"
    schema = None

    async def check(self, spec, ctx):
        return RequirementResult(status="missing", detail="absent on this host")


class _BackendScopedMissingOnChecker:
    """A "backend"-scoped checker whose verdict is "missing" for whichever
    backend ids `spec["missing_on"]` names, "ok" otherwise."""

    type = "routing_test_backend_scoped"
    schema = None
    scope = "backend"

    async def check(self, spec, ctx):
        backend_id = ctx.backend.id if ctx.backend else None
        if backend_id in spec.get("missing_on", []):
            return RequirementResult(status="missing", detail=f"absent on {backend_id}")
        return RequirementResult(status="ok", detail=f"present on {backend_id}")


class TestRequirementsEligibilityRealCacheProjection:
    """Regression cover for the routing projection bug: `peek_backend_missing`
    used to read the merged per-backend cache entry with no scope filter, so
    one host-scoped miss (evaluated once, shared by every backend) dropped
    every backend of the engine identically. These fixtures go through the
    REAL `RequirementsCache` and the REAL `RequirementsEligibility` rule (no
    mocked cache) so the projection itself is exercised, not a stand-in."""

    def setup_method(self):
        for checker_cls in (_AlwaysMissingHostChecker, _BackendScopedMissingOnChecker):
            if requirement_checker_registry.get(checker_cls.type) is None:
                requirement_checker_registry.register(
                    RequirementCheckerRegistration(type_name=checker_cls.type, checker=checker_cls(), source="test")
                )

    @staticmethod
    async def _cache_for(preset, backend_ids):
        host_ctx = build_requirement_context_for_backend(preset, None, None, None)
        backend_ctxs = {
            bid: build_requirement_context_for_backend(
                preset, None, None, RequirementBackendInfo(id=bid, engine="comfyui", driver="comfyui")
            )
            for bid in backend_ids
        }
        cache = RequirementsCache()
        await cache.get_or_evaluate_for_backends(requirement_checker_registry, preset, host_ctx, backend_ctxs)
        return cache

    @pytest.mark.asyncio
    async def test_host_scoped_miss_never_narrows_both_backends_stay_eligible(self):
        preset = Mock(id="host-miss-preset", requirements=[{"type": _AlwaysMissingHostChecker.type}])
        cache = await self._cache_for(preset, ["comfy_a", "comfy_b"])

        candidates = _candidates("comfy_a", "comfy_b")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="comfyui", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        assert not any(c.dropped for c in result)
        # the host miss is still visible everywhere else - only routing's
        # projection must hide it.
        assert cache.peek_summary(preset, backend_id="comfy_a")["summary"]["missing"] == 1
        assert cache.peek_summary(preset, backend_id="comfy_b")["summary"]["missing"] == 1
        assert cache.peek_host_summary(requirement_checker_registry, preset)["summary"]["missing"] == 1

    @pytest.mark.asyncio
    async def test_backend_scoped_miss_excludes_only_that_backend(self):
        preset = Mock(
            id="backend-miss-preset",
            requirements=[{"type": _BackendScopedMissingOnChecker.type, "missing_on": ["comfy_a"]}],
        )
        cache = await self._cache_for(preset, ["comfy_a", "comfy_b"])

        candidates = _candidates("comfy_a", "comfy_b")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="comfyui", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        by_id = {c.backend_id: c for c in result}
        assert by_id["comfy_a"].dropped
        assert not by_id["comfy_b"].dropped

    @pytest.mark.asyncio
    async def test_mixed_host_and_backend_miss_excludes_only_the_backend_scoped_one(self):
        preset = Mock(
            id="mixed-miss-preset",
            requirements=[
                {"type": _AlwaysMissingHostChecker.type},
                {"type": _BackendScopedMissingOnChecker.type, "missing_on": ["comfy_a"]},
            ],
        )
        cache = await self._cache_for(preset, ["comfy_a", "comfy_b"])

        candidates = _candidates("comfy_a", "comfy_b")
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache)
        request = RoutingRequest(engine="comfyui", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        by_id = {c.backend_id: c for c in result}
        assert by_id["comfy_a"].dropped
        assert not by_id["comfy_b"].dropped

    @pytest.mark.asyncio
    async def test_optional_miss_stays_eligible_and_cold_backend_is_unknown_with_real_cache(self):
        preset = Mock(
            id="optional-and-cold-preset",
            requirements=[
                {"type": _BackendScopedMissingOnChecker.type, "missing_on": ["comfy_a"], "optional": True},
            ],
        )
        # Only "comfy_a" gets evaluated into the cache - "comfy_b" stays cold.
        cache = await self._cache_for(preset, ["comfy_a"])

        candidates = _candidates("comfy_a", "comfy_b")
        scheduled = []
        ctx = RoutingContext(backend_registry=Mock(), requirements_cache=cache, schedule_background=scheduled.append)
        request = RoutingRequest(engine="comfyui", preset=preset, form_data={})

        result = await RequirementsEligibility().apply(candidates, request, ctx)

        by_id = {c.backend_id: c for c in result}
        assert not by_id["comfy_a"].dropped  # optional miss never drops
        assert not by_id["comfy_b"].dropped  # never evaluated -> unknown, kept
        assert "not yet checked" in by_id["comfy_b"].reasons[-1]
        assert len(scheduled) == 1
        scheduled[0].close()  # never awaited by design here - avoid the GC warning
