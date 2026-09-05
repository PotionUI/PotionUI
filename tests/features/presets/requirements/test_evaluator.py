"""evaluate_preset_requirements: unregistered type -> unknown, timeout ->
unknown, never raises; RequirementsCache: hit/miss, refresh, fingerprint
invalidation on a requirements-block edit.
"""

import asyncio

import pytest

from src.features.presets.requirements.contracts import RequirementContext, RequirementResult
from src.features.presets.requirements.evaluator import (
    RequirementsCache,
    entry_scope,
    evaluate_preset_requirements,
    evaluate_preset_requirements_for_backends,
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
    async def test_checker_declared_timeout_s_overrides_the_default(self, monkeypatch):
        """A checker's own `timeout_s` (e.g. a slow-but-legitimate network
        check) wins over the evaluator's default - lowering
        CHECK_TIMEOUT_SECONDS below the checker's `timeout_s` must not make
        it time out early."""
        import src.features.presets.requirements.evaluator as evaluator_module
        monkeypatch.setattr(evaluator_module, "CHECK_TIMEOUT_SECONDS", 0.05)

        class _SlowButPatientChecker(_HangingChecker):
            type = "slow-patient-type"
            timeout_s = 10.0

            async def check(self, spec, ctx):
                await asyncio.sleep(0.1)
                return RequirementResult(status="ok", detail="took a moment, but made it")

        registry = _registry(_SlowButPatientChecker())
        preset = _preset(requirements=[{"type": "slow-patient-type"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert results[0].status == "ok"

    @pytest.mark.asyncio
    async def test_checker_declared_timeout_s_still_times_out_eventually(self):
        class _StillTooSlowChecker(_HangingChecker):
            type = "still-too-slow-type"
            timeout_s = 0.05

        registry = _registry(_StillTooSlowChecker())
        preset = _preset(requirements=[{"type": "still-too-slow-type"}])

        results = await evaluate_preset_requirements(registry, preset, _ctx())

        assert results[0].status == "unknown"
        assert "did not complete within 0.05s" in results[0].detail

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
        specs = [{"type": "a"}, {"type": "b"}, {"type": "c"}, {"type": "d"}]
        results = [
            RequirementResult(status="ok", detail=""),
            RequirementResult(status="ok", detail=""),
            RequirementResult(status="missing", detail=""),
            RequirementResult(status="unknown", detail=""),
        ]
        assert summarize(specs, results) == {"ok": 2, "missing": 1, "unknown": 1, "optional_missing": 0}

    def test_optional_miss_counts_separately_from_missing(self):
        specs = [
            {"type": "a", "optional": False},
            {"type": "b", "optional": True},
            {"type": "c", "optional": True},
        ]
        results = [
            RequirementResult(status="missing", detail=""),
            RequirementResult(status="missing", detail=""),
            RequirementResult(status="ok", detail=""),
        ]
        assert summarize(specs, results) == {"ok": 1, "missing": 1, "unknown": 0, "optional_missing": 1}

    def test_optional_ok_and_unknown_are_not_reclassified(self):
        specs = [{"type": "a", "optional": True}, {"type": "b", "optional": True}]
        results = [
            RequirementResult(status="ok", detail=""),
            RequirementResult(status="unknown", detail=""),
        ]
        assert summarize(specs, results) == {"ok": 1, "missing": 0, "unknown": 1, "optional_missing": 0}


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
        assert cache.peek_summary(preset) == {
            "summary": summarize(preset.requirements, results1), "checked_at": checked_at1,
        }

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


class _HostChecker:
    """A "host"-scoped checker (the default) - counts calls so tests can
    assert it runs once per preset, not once per backend."""

    type = "host-type"
    schema = None

    def __init__(self):
        self.calls = 0

    async def check(self, spec, ctx):
        self.calls += 1
        return RequirementResult(status="ok", detail="host fine")


class _MissingHostChecker:
    """A "host"-scoped checker (the default) whose verdict is always
    "missing" - lets a test simulate a host-wide binary/package miss."""

    type = "missing-host-type"
    schema = None

    async def check(self, spec, ctx):
        return RequirementResult(status="missing", detail="absent on this host")


class _BackendChecker:
    """A "backend"-scoped checker whose answer depends on `ctx.backend.id` -
    "ok" for `ok_backend_id`, "missing" for anything else."""

    type = "backend-type"
    schema = None
    scope = "backend"

    def __init__(self, ok_backend_id: str):
        self.ok_backend_id = ok_backend_id
        self.calls = []

    async def check(self, spec, ctx):
        backend_id = ctx.backend.id if ctx.backend else None
        self.calls.append(backend_id)
        if backend_id == self.ok_backend_id:
            return RequirementResult(status="ok", detail=f"present on {backend_id}")
        return RequirementResult(status="missing", detail=f"absent on {backend_id}")


def _backend_ctx(backend_id: str) -> RequirementContext:
    from src.features.presets.requirements.contracts import RequirementBackendInfo

    return RequirementContext(
        models=None, gpu_available=False, gpu_total_vram_gb=None,
        backend=RequirementBackendInfo(id=backend_id, engine="comfyui", driver="comfyui", name=backend_id),
        platform="linux",
    )


class TestEntryScope:
    def test_unregistered_type_is_host_scope(self):
        registry = _registry()
        assert entry_scope(registry, {"type": "no-such-checker"}) == "host"

    def test_checker_without_scope_attribute_is_host(self):
        registry = _registry(_OkChecker())
        assert entry_scope(registry, {"type": "ok-type"}) == "host"

    def test_checker_declaring_backend_scope_is_backend(self):
        registry = _registry(_BackendChecker(ok_backend_id="b1"))
        assert entry_scope(registry, {"type": "backend-type"}) == "backend"

    def test_vram_min_gb_is_backend_scoped(self):
        """A preset's declared VRAM floor is a property of whichever
        backend would actually run it (a local GPU reading does not apply
        to a remote worker of the same engine) - it must not be shared
        across every candidate the way a genuinely host-wide check is."""
        from src.features.presets.requirements.builtin import VramMinGbRequirementChecker

        registry = _registry(VramMinGbRequirementChecker())
        assert entry_scope(registry, {"type": "vram_min_gb", "gb": 16}) == "backend"


class TestEvaluatePresetRequirementsForBackends:
    @pytest.mark.asyncio
    async def test_host_scoped_entry_runs_once_not_per_backend(self):
        host_checker = _HostChecker()
        registry = _registry(host_checker)
        preset = _preset(requirements=[{"type": "host-type"}])

        evaluation = await evaluate_preset_requirements_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert host_checker.calls == 1
        assert evaluation.full_results("b1")[0].status == "ok"
        assert evaluation.full_results("b2")[0].status == "ok"

    @pytest.mark.asyncio
    async def test_backend_scoped_entry_runs_once_per_backend_with_its_own_verdict(self):
        backend_checker = _BackendChecker(ok_backend_id="b1")
        registry = _registry(backend_checker)
        preset = _preset(requirements=[{"type": "backend-type"}])

        evaluation = await evaluate_preset_requirements_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert sorted(backend_checker.calls) == ["b1", "b2"]
        assert evaluation.full_results("b1")[0].status == "ok"
        assert evaluation.full_results("b2")[0].status == "missing"

    @pytest.mark.asyncio
    async def test_mixed_host_and_backend_entries_merge_back_in_spec_order(self):
        host_checker = _HostChecker()
        backend_checker = _BackendChecker(ok_backend_id="b1")
        registry = _registry(host_checker, backend_checker)
        preset = _preset(requirements=[{"type": "host-type"}, {"type": "backend-type"}])

        evaluation = await evaluate_preset_requirements_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert host_checker.calls == 1
        assert [r.status for r in evaluation.full_results("b1")] == ["ok", "ok"]
        assert [r.status for r in evaluation.full_results("b2")] == ["ok", "missing"]
        assert evaluation.summary_for("b1") == {"ok": 2, "missing": 0, "unknown": 0, "optional_missing": 0}
        assert evaluation.summary_for("b2") == {"ok": 1, "missing": 1, "unknown": 0, "optional_missing": 0}

    @pytest.mark.asyncio
    async def test_no_backends_falls_back_to_host_only_results(self):
        host_checker = _HostChecker()
        backend_checker = _BackendChecker(ok_backend_id="b1")
        registry = _registry(host_checker, backend_checker)
        preset = _preset(requirements=[{"type": "host-type"}, {"type": "backend-type"}])

        evaluation = await evaluate_preset_requirements_for_backends(registry, preset, _ctx(), {})

        assert backend_checker.calls == []
        results = evaluation.full_results(None)
        assert results[0].status == "ok"
        assert results[1].status == "unknown"

    @pytest.mark.asyncio
    async def test_empty_requirements_short_circuits(self):
        registry = _registry()
        preset = _preset(requirements=[])

        evaluation = await evaluate_preset_requirements_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1")},
        )

        assert evaluation.full_results("b1") == []
        assert evaluation.summary_for("b1") == {"ok": 0, "missing": 0, "unknown": 0, "optional_missing": 0}


class TestRequirementsCacheForBackends:
    @pytest.mark.asyncio
    async def test_host_scoped_entry_is_evaluated_once_across_calls_and_backends(self):
        host_checker = _HostChecker()
        backend_checker = _BackendChecker(ok_backend_id="b1")
        registry = _registry(host_checker, backend_checker)
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "host-type"}, {"type": "backend-type"}])

        await cache.get_or_evaluate_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )
        await cache.get_or_evaluate_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert host_checker.calls == 1
        assert sorted(backend_checker.calls) == ["b1", "b2"]

    @pytest.mark.asyncio
    async def test_refresh_reevaluates_host_and_every_backend(self):
        host_checker = _HostChecker()
        backend_checker = _BackendChecker(ok_backend_id="b1")
        registry = _registry(host_checker, backend_checker)
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "host-type"}, {"type": "backend-type"}])
        backend_ctxs = {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")}

        await cache.get_or_evaluate_for_backends(registry, preset, _ctx(), backend_ctxs)
        await cache.get_or_evaluate_for_backends(registry, preset, _ctx(), backend_ctxs, refresh=True)

        assert host_checker.calls == 2
        assert backend_checker.calls == ["b1", "b2", "b1", "b2"]

    @pytest.mark.asyncio
    async def test_populates_the_legacy_by_backend_slot_peek_summary_reads(self):
        registry = _registry(_BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "backend-type"}])

        await cache.get_or_evaluate_for_backends(registry, preset, _ctx(), {"b1": _backend_ctx("b1")})

        assert cache.peek_summary(preset, backend_id="b1") == {
            "summary": {"ok": 1, "missing": 0, "unknown": 0, "optional_missing": 0},
            "checked_at": cache.peek_summary(preset, backend_id="b1")["checked_at"],
        }

    @pytest.mark.asyncio
    async def test_peek_host_summary_reflects_only_host_scoped_entries(self):
        registry = _registry(_HostChecker(), _BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "host-type"}, {"type": "backend-type"}])

        assert cache.peek_host_summary(registry, preset) is None

        await cache.get_or_evaluate_for_backends(registry, preset, _ctx(), {"b1": _backend_ctx("b1")})

        peek = cache.peek_host_summary(registry, preset)
        assert peek["summary"] == {"ok": 1, "missing": 0, "unknown": 0, "optional_missing": 0}

    @pytest.mark.asyncio
    async def test_peek_backend_missing_is_none_before_evaluation(self):
        registry = _registry(_BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "backend-type"}])

        assert cache.peek_backend_missing(registry, preset, "b2") is None

    @pytest.mark.asyncio
    async def test_peek_backend_missing_lists_hard_missing_requirement_names(self):
        registry = _registry(_BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "backend-type"}])

        await cache.get_or_evaluate_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert cache.peek_backend_missing(registry, preset, "b1") == []
        assert cache.peek_backend_missing(registry, preset, "b2") == ["backend-type"]

    @pytest.mark.asyncio
    async def test_peek_backend_missing_excludes_optional_misses(self):
        registry = _registry(_BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "backend-type", "optional": True}])

        await cache.get_or_evaluate_for_backends(registry, preset, _ctx(), {"b2": _backend_ctx("b2")})

        assert cache.peek_backend_missing(registry, preset, "b2") == []

    @pytest.mark.asyncio
    async def test_peek_backend_missing_excludes_host_scoped_misses(self):
        """A host-scoped requirement's miss is evaluated once and merged
        into every backend's cached entry (see `full_results`) - it must
        NOT be projected into `peek_backend_missing`, or one missing
        host-wide binary would look like a per-backend miss on every
        backend of the engine."""
        registry = _registry(_MissingHostChecker())
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "missing-host-type"}])

        await cache.get_or_evaluate_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        # The host miss is still visible everywhere else...
        assert cache.peek_summary(preset, backend_id="b1")["summary"]["missing"] == 1
        assert cache.peek_host_summary(registry, preset)["summary"]["missing"] == 1
        # ...but never narrows routing eligibility via peek_backend_missing.
        assert cache.peek_backend_missing(registry, preset, "b1") == []
        assert cache.peek_backend_missing(registry, preset, "b2") == []

    @pytest.mark.asyncio
    async def test_peek_backend_missing_mixed_host_and_backend_miss(self):
        """A host-scoped miss plus a genuinely backend-scoped miss on one
        backend: only the backend-scoped one is projected, and only for the
        backend it's actually missing on."""
        registry = _registry(_MissingHostChecker(), _BackendChecker(ok_backend_id="b1"))
        cache = RequirementsCache()
        preset = _preset(requirements=[{"type": "missing-host-type"}, {"type": "backend-type"}])

        await cache.get_or_evaluate_for_backends(
            registry, preset, _ctx(), {"b1": _backend_ctx("b1"), "b2": _backend_ctx("b2")},
        )

        assert cache.peek_backend_missing(registry, preset, "b1") == []
        assert cache.peek_backend_missing(registry, preset, "b2") == ["backend-type"]
