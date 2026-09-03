"""evaluate_preset_requirements: unregistered type -> unknown, timeout ->
unknown, never raises; RequirementsCache: hit/miss, refresh, fingerprint
invalidation on a requirements-block edit.
"""

import asyncio

import pytest

from src.features.presets.requirements.contracts import RequirementContext, RequirementResult
from src.features.presets.requirements.evaluator import (
    RequirementsCache,
    evaluate_preset_requirements,
    preset_requirements_fingerprint,
    summarize,
)
from src.features.presets.templates import PresetTemplate
from src.platform.plugins.requirement_checkers import (
    RequirementCheckerRegistration,
    RequirementCheckerRegistry,
)


def _ctx() -> RequirementContext:
    return RequirementContext(models=None, gpu_available=False, gpu_total_vram_gb=None, backend=None, platform="linux")


def _preset(requirements=None, preset_id="preset-1") -> PresetTemplate:
    return PresetTemplate(
        id=preset_id, name="Preset", version="1.0.0", path="/tmp/preset", modes={},
        requirements=requirements or [],
    )


class _OkChecker:
    type = "ok-type"
    schema = None

    async def check(self, spec, ctx):
        return RequirementResult(status="ok", detail="fine")


class _HangingChecker:
    type = "hang-type"
    schema = None

    async def check(self, spec, ctx):
        await asyncio.sleep(10)
        return RequirementResult(status="ok", detail="never gets here")


class _RaisingChecker:
    type = "raise-type"
    schema = None

    async def check(self, spec, ctx):
        raise RuntimeError("boom")


def _registry(*checkers) -> RequirementCheckerRegistry:
    registry = RequirementCheckerRegistry()
    for checker in checkers:
        registry.register(RequirementCheckerRegistration(type_name=checker.type, checker=checker, source="core"))
    return registry


class TestEvaluatePresetRequirements:
    @pytest.mark.asyncio
    async def test_unregistered_type_is_unknown_not_missing(self):
        registry = _registry()
        preset = _preset(requirements=[{"type": "no-such-checker"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert len(results) == 1
        assert results[0].status == "unknown"

    @pytest.mark.asyncio
    async def test_timeout_resolves_to_unknown(self, monkeypatch):
        import src.features.presets.requirements.evaluator as evaluator_module
        monkeypatch.setattr(evaluator_module, "CHECK_TIMEOUT_SECONDS", 0.05)
        registry = _registry(_HangingChecker())
        preset = _preset(requirements=[{"type": "hang-type"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert results[0].status == "unknown"
        assert "did not complete" in results[0].detail

    @pytest.mark.asyncio
    async def test_raising_checker_resolves_to_unknown_not_raise(self):
        registry = _registry(_RaisingChecker())
        preset = _preset(requirements=[{"type": "raise-type"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert results[0].status == "unknown"

    @pytest.mark.asyncio
    async def test_runs_multiple_entries_concurrently(self):
        registry = _registry(_OkChecker())
        preset = _preset(requirements=[{"type": "ok-type"}, {"type": "ok-type"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert [r.status for r in results] == ["ok", "ok"]

    def test_no_requirements_short_circuits(self):
        registry = _registry()
        preset = _preset(requirements=[])

        results = asyncio.run(evaluate_preset_requirements(registry, preset, _ctx()))

        assert results == []


class TestSummarize:
    def test_counts_by_status(self):
        results = [
            RequirementResult(status="ok", detail=""),
            RequirementResult(status="ok", detail=""),
            RequirementResult(status="missing", detail=""),
            RequirementResult(status="unknown", detail=""),
        ]
        assert summarize(results) == {"ok": 2, "missing": 1, "unknown": 1}


class TestPresetRequirementsFingerprint:
    def test_stable_for_same_content(self):
        preset_a = _preset(requirements=[{"type": "binary", "name": "ffmpeg"}])
        preset_b = _preset(requirements=[{"type": "binary", "name": "ffmpeg"}], preset_id="different-id")

        assert preset_requirements_fingerprint(preset_a) == preset_requirements_fingerprint(preset_b)

    def test_changes_when_requirements_edited(self):
        preset_a = _preset(requirements=[{"type": "binary", "name": "ffmpeg"}])
        preset_b = _preset(requirements=[{"type": "binary", "name": "imagemagick"}])

        assert preset_requirements_fingerprint(preset_a) != preset_requirements_fingerprint(preset_b)


class TestRequirementsCache:
    @pytest.mark.asyncio
    async def test_peek_before_evaluation_is_none(self):
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "ok-type"}])

        assert cache.peek_summary(preset) is None

    @pytest.mark.asyncio
    async def test_get_or_evaluate_caches_result(self):
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "ok-type"}])
        calls = {"n": 0}

        class _CountingChecker(_OkChecker):
            async def check(self, spec, ctx):
                calls["n"] += 1
                return await super().check(spec, ctx)

        registry = _registry(_CountingChecker())

        results1, checked_at1 = await cache.get_or_evaluate(registry, preset, _ctx())
        results2, checked_at2 = await cache.get_or_evaluate(registry, preset, _ctx())

        assert calls["n"] == 1
        assert checked_at1 == checked_at2
        assert cache.peek_summary(preset) == {"summary": summarize(results1), "checked_at": checked_at1}

    @pytest.mark.asyncio
    async def test_refresh_forces_reevaluation(self):
        calls = {"n": 0}

        class _CountingChecker(_OkChecker):
            async def check(self, spec, ctx):
                calls["n"] += 1
                return await super().check(spec, ctx)

        registry = _registry(_CountingChecker())
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "ok-type"}])

        await cache.get_or_evaluate(registry, preset, _ctx())
        await cache.get_or_evaluate(registry, preset, _ctx(), refresh=True)

        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_editing_requirements_invalidates_cache_key(self):
        registry = _registry(_OkChecker())
        cache = RequirementsCache()
        preset_v1 = _preset(requirements=[{"type": "ok-type"}])
        preset_v2 = _preset(requirements=[{"type": "ok-type", "hint": "changed"}])

        await cache.get_or_evaluate(registry, preset_v1, _ctx())

        assert cache.peek_summary(preset_v2) is None

    @pytest.mark.asyncio
    async def test_different_backend_ids_are_cached_separately(self):
        registry = _registry(_OkChecker())
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "ok-type"}])

        await cache.get_or_evaluate(registry, preset, _ctx(), backend_id="backend-a")

        assert cache.peek_summary(preset, backend_id="backend-a") is not None
        assert cache.peek_summary(preset, backend_id="backend-b") is None
