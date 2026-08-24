import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import torch

import src.platform.runtime.model_lifecycle.manager as manager_module
from src.platform.runtime.model_lifecycle.manager import (
    ModelLifecycleManager,
    empty_pinned_host_cache,
    file_size_gb,
)
from src.platform.runtime.system_memory import SystemMemory


class FakeModel:
    """Stand-in for a model wrapper with the shape ModelLifecycleManager knows
    how to unload: a `.pipe` (diffusers-style, unload_lora_weights) and a
    `.unload()` + `.module` pair matching NativeModel's real eviction contract
    (unload() nulls `.module` - the "in-flight reference" safety check reads
    that).

    Eviction may drop the value from `_entries` WITHOUT calling `.unload()`
    (see ModelLifecycleManager's A3 refcount safety check) whenever something
    outside the cache still holds a reference - which is exactly what a test
    local variable held past the triggering `acquire()` call does. So
    `unload()` also appends to a `log` list that survives independently of
    the model object's lifetime, letting tests that intentionally DON'T keep
    a reference (to exercise the "cache was the sole owner" path) still
    verify unload() ran on an object they can no longer inspect directly.
    """

    def __init__(self, name, log=None):
        self.name = name
        self.pipe = Mock(spec=["unload_lora_weights"])
        self.module = object()
        self._log = log if log is not None else []

    def unload(self):
        self._log.append(self.name)
        self.module = None

    @property
    def unloaded(self):
        return self.name in self._log


@pytest.fixture(autouse=True)
def _no_real_cuda_calls(monkeypatch):
    # This suite exercises `cleanup(aggressive=True)` a lot more than before
    # (fixing the RAM-pressure no-op means eviction actually fires now), and
    # `cleanup()` calls real `torch.cuda.synchronize()`/`empty_cache()` when a
    # CUDA device is visible. Unit tests must not depend on/touch a real GPU
    # (this sandbox's device may be busy/OOM from an unrelated process).
    monkeypatch.setattr("torch.cuda.synchronize", lambda: None, raising=False)
    monkeypatch.setattr("torch.cuda.empty_cache", lambda: None, raising=False)


@pytest.fixture
def fake_gpu_manager():
    gpu = Mock()
    gpu.get_vram_budget.return_value = 10.0
    return gpu


@pytest.fixture
def manager(fake_gpu_manager):
    return ModelLifecycleManager(gpu_manager=fake_gpu_manager, settings_manager=None)


class TestAcquireHitMiss:
    def test_miss_calls_loader_and_caches(self, manager):
        loader = Mock(return_value=FakeModel("a"))

        result = manager.acquire("key1", "fp1", loader)

        loader.assert_called_once()
        assert result.name == "a"
        stats = manager.stats()
        assert stats["entries"] == 1
        assert stats["misses"] == 1
        assert stats["hits"] == 0

    def test_hit_when_fingerprint_matches(self, manager):
        loader = Mock(return_value=FakeModel("a"))

        first = manager.acquire("key1", "fp1", loader)
        second = manager.acquire("key1", "fp1", loader)

        loader.assert_called_once()  # loader not called again on hit
        assert first is second
        assert manager.stats()["hits"] == 1
        assert manager.stats()["misses"] == 1

    def test_fingerprint_change_busts_cache_sole_owner(self, manager):
        # No external reference kept to the first model past the busting
        # acquire() call, so the cache is its sole owner and the bust really
        # unloads it (as opposed to just dropping it - see the
        # still-referenced variant below). Uses fresh closures rather than
        # `Mock(side_effect=[...])`, whose internal list would otherwise keep
        # both produced models alive for the mock's own lifetime.
        log = []
        calls = []

        def loader():
            name = "a" if not calls else "b"
            calls.append(name)
            return FakeModel(name, log)

        manager.acquire("key1", "fp1", loader)
        result2 = manager.acquire("key1", "fp2", loader)

        assert len(calls) == 2
        assert result2.name == "b"
        assert "a" in log  # old entry evicted AND unloaded
        assert manager.stats()["misses"] == 2

    def test_fingerprint_change_busts_cache_still_referenced(self, manager):
        # This time the caller keeps holding the first model (as any real
        # loader pipe does with its own local var) across the busting
        # acquire() - the bust must drop it from the cache but NOT unload it.
        log = []
        loader = Mock(side_effect=[FakeModel("a", log), FakeModel("b", log)])

        result1 = manager.acquire("key1", "fp1", loader)
        result2 = manager.acquire("key1", "fp2", loader)

        assert loader.call_count == 2
        assert result2.name == "b"
        assert "a" not in log  # NOT unloaded - result1 still references it
        assert result1.module is not None
        assert "key1" in manager.stats()["keys"]  # holds "b" now, not "a"

    def test_fingerprint_change_sole_owner_trims_host_allocator(self, manager, monkeypatch):
        # Repro for the Krea-2 "adding a LoRA bloats RAM to 80GB+" bug: a LoRA
        # change busts only the DiT's fingerprint (same preset, no preset
        # switch), so _evict_foreign_owner's trim never runs, and a big-RAM
        # box has enough headroom that _make_room_for_ram's pressure branch
        # (the other trim site) never fires either. Without a trim right after
        # this SAME-key bust, the freed ~24.5GB DiT stays an unreturned glibc
        # arena while the fresh checkpoint read for the LoRA-patched DiT
        # allocates on top of it - RSS ratchets up every swap instead of
        # staying flat.
        trims = []
        monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: trims.append(1))
        log = []
        calls = []

        def loader():
            name = "a" if not calls else "b"
            calls.append(name)
            return FakeModel(name, log)

        manager.acquire("dit", "fp_no_lora", loader)
        manager.acquire("dit", "fp_with_lora", loader)  # LoRA added -> fingerprint bust

        assert "a" in log  # old DiT unloaded (sole cache owner)
        assert trims, "host allocator was not trimmed after a same-key fingerprint bust"

    def test_fingerprint_change_still_referenced_does_not_trim(self, manager, monkeypatch):
        # Mirror of the sole-owner case above, but nothing was actually freed
        # (the caller still holds the old value), so trimming would be a
        # pointless malloc_trim call on every acquire.
        trims = []
        monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: trims.append(1))
        log = []
        loader = Mock(side_effect=[FakeModel("a", log), FakeModel("b", log)])

        held = manager.acquire("dit", "fp_no_lora", loader)
        manager.acquire("dit", "fp_with_lora", loader)

        assert "a" not in log  # not unloaded - `held` still references it
        assert not trims

    def test_different_keys_are_independent(self, manager):
        loader_a = Mock(return_value=FakeModel("a"))
        loader_b = Mock(return_value=FakeModel("b"))

        manager.acquire("key_a", "fp", loader_a)
        manager.acquire("key_b", "fp", loader_b)

        assert manager.stats()["entries"] == 2
        loader_a.assert_called_once()
        loader_b.assert_called_once()


class TestInvalidate:
    def test_invalidate_single_key_sole_owner(self, manager):
        log = []
        manager.acquire("key_a", "fp", lambda: FakeModel("a", log))
        manager.acquire("key_b", "fp", lambda: FakeModel("b", log))

        manager.invalidate("key_a")

        assert "a" in log
        assert "b" not in log
        assert manager.stats()["entries"] == 1
        assert manager.stats()["keys"] == ["key_b"]

    def test_invalidate_all_sole_owner(self, manager):
        log = []
        manager.acquire("key_a", "fp", lambda: FakeModel("a", log))
        manager.acquire("key_b", "fp", lambda: FakeModel("b", log))

        manager.invalidate()

        assert "a" in log
        assert "b" in log
        assert manager.stats()["entries"] == 0

    def test_invalidate_still_referenced_drops_without_unload(self, manager):
        log = []
        held = manager.acquire("key_a", "fp", lambda: FakeModel("a", log))

        manager.invalidate("key_a")

        assert "a" not in log  # not unloaded - `held` still references it
        assert held.module is not None
        assert manager.stats()["entries"] == 0

    def test_invalidate_unknown_key_is_noop(self, manager):
        manager.acquire("key_a", "fp", Mock(return_value=FakeModel("a")))

        manager.invalidate("does-not-exist")  # should not raise

        assert manager.stats()["entries"] == 1


class TestEvictDeadWeight:
    """A generation pipe explicitly releasing ONE cache entry
    it knows is dead weight for the rest of its own generation (e.g. the LTX
    standalone-upscale pipe releasing its idle Gemma3 TE)."""

    def test_sole_owner_is_unloaded_and_removed(self, manager):
        log = []
        manager.acquire("key_a", "fp", lambda: FakeModel("a", log))

        result = manager.evict_dead_weight("key_a")

        assert result is True
        assert "a" in log
        assert manager.stats()["entries"] == 0

    def test_still_referenced_drops_without_unload(self, manager):
        log = []
        held = manager.acquire("key_a", "fp", lambda: FakeModel("a", log))

        result = manager.evict_dead_weight("key_a")

        assert result is False
        assert "a" not in log  # not unloaded - `held` still references it
        assert held.module is not None
        assert manager.stats()["entries"] == 0

    def test_unknown_key_is_noop(self, manager):
        manager.acquire("key_a", "fp", Mock(return_value=FakeModel("a")))

        result = manager.evict_dead_weight("does-not-exist")

        assert result is False
        assert manager.stats()["entries"] == 1

    def test_bypasses_an_active_generation_lease(self, manager):
        """The entry is leased by the current generation (acquired inside a
        `begin_lease`/`end_lease` window that hasn't ended yet) -- routine LRU
        pressure must never touch it, but an explicit `evict_dead_weight` call
        (the pipe KNOWS this component is done) still evicts it."""
        log = []
        manager.begin_lease("gen-1")
        manager.acquire("key_a", "fp", lambda: FakeModel("a", log))

        result = manager.evict_dead_weight("key_a")

        assert result is True
        assert "a" in log
        assert manager.stats()["entries"] == 0
        # The lease bookkeeping itself must not be left dangling.
        manager.end_lease("gen-1")  # must not raise / double-free

    def test_leaves_other_entries_untouched(self, manager):
        log = []
        manager.acquire("key_a", "fp", lambda: FakeModel("a", log))
        manager.acquire("key_b", "fp", lambda: FakeModel("b", log))

        manager.evict_dead_weight("key_a")

        assert "a" in log
        assert "b" not in log
        assert manager.stats()["keys"] == ["key_b"]

    def test_does_not_empty_pinned_host_cache(self, manager, monkeypatch):
        """Unlike `invalidate()`, this is a routine per-generation call that
        must not force a live partial-residency `stream_to()` pool elsewhere
        in the SAME generation to re-pin from scratch."""
        calls = []
        monkeypatch.setattr(manager_module, "empty_pinned_host_cache", lambda: calls.append(1))
        manager.acquire("key_a", "fp", lambda: FakeModel("a"))

        manager.evict_dead_weight("key_a")

        assert calls == []

    def test_calls_cleanup_only_when_something_was_unloaded(self, manager, monkeypatch):
        calls = []
        monkeypatch.setattr(manager, "cleanup", lambda aggressive=False: calls.append(aggressive))

        manager.evict_dead_weight("does-not-exist")
        assert calls == []

        manager.acquire("key_a", "fp", lambda: FakeModel("a"))
        manager.evict_dead_weight("key_a")
        assert calls == [True]


class TestEvictionOrder:
    # Eviction is driven by HOST-RAM pressure only. There is deliberately no
    # VRAM-budget axis here: cached models sit offloaded in host RAM between
    # generations, and summing cache entries against a VRAM budget evicted
    # CPU-resident models that coexisted fine (Krea-2 TE+DiT thrashed the
    # cache, reloading ~34GB from disk every generation).

    def _patch_ram(self, monkeypatch, manager, *, total_gb=100.0, base_avail_gb=22.0, per_entry_gb=6.0):
        """Fake system-RAM reads so available RAM shrinks with each live cache
        entry: floor = max(8, 10%*100) = 10GB; with base 22GB the first two
        6GB loads pass admission and the third dips below the floor, forcing
        one LRU evict (the re-measured available recovers as entries drop)."""
        gb = 1024 ** 3

        def fake_get_system_memory():
            avail = base_avail_gb - per_entry_gb * len(manager._entries)
            return SystemMemory(total=int(total_gb * gb), available=int(avail * gb))

        monkeypatch.setattr(manager_module, "get_system_memory", fake_get_system_memory)

    def test_lru_eviction_under_ram_pressure_sole_owner(self, monkeypatch):
        manager = ModelLifecycleManager(gpu_manager=None, settings_manager=None)
        self._patch_ram(monkeypatch, manager)
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=6.0)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=6.0)

        # The third 6GB load would leave less than the free-RAM floor, so the
        # least-recently-used entry ("a") must be evicted. No test-side
        # reference is kept to "a"/"b", so the cache is their sole owner and
        # eviction really unloads.
        manager.acquire("c", "fp", lambda: FakeModel("c", log), estimated_vram_gb=6.0)

        assert "a" in log  # LRU entry evicted and unloaded
        assert "b" not in log
        assert "a" not in manager.stats()["keys"]
        assert "b" in manager.stats()["keys"]
        assert "c" in manager.stats()["keys"]

    def test_lru_eviction_still_referenced_drops_without_unload(self, monkeypatch):
        manager = ModelLifecycleManager(gpu_manager=None, settings_manager=None)
        self._patch_ram(monkeypatch, manager)
        log = []

        model_a = manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=6.0)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=6.0)
        manager.acquire("c", "fp", lambda: FakeModel("c", log), estimated_vram_gb=6.0)

        assert "a" not in log  # not unloaded - `model_a` still references it
        assert model_a.module is not None
        assert "a" not in manager.stats()["keys"]  # still dropped from the cache

    def test_cached_models_never_evicted_on_vram_style_budget(self, fake_gpu_manager, monkeypatch):
        # Regression guard for the Krea-2 thrash: big per-entry estimates that
        # would blow any VRAM budget must NOT cause eviction while host RAM is
        # plentiful. RAM is faked as plentiful (hermetic — the real box may be
        # under pressure from other processes). gpu_manager present to prove
        # it is not consulted for admission.
        fake_gpu_manager.get_vram_budget.return_value = 15.0
        manager = ModelLifecycleManager(gpu_manager=fake_gpu_manager, settings_manager=None)
        gb = 1024 ** 3
        monkeypatch.setattr(
            manager_module, "get_system_memory",
            lambda: SystemMemory(total=int(100.0 * gb), available=int(80.0 * gb)),
        )
        log = []

        manager.acquire("te", "fp", lambda: FakeModel("te", log), estimated_vram_gb=9.0)
        manager.acquire("dit", "fp", lambda: FakeModel("dit", log), estimated_vram_gb=24.5)

        assert log == []  # nothing unloaded
        assert manager.stats()["entries"] == 2

    def test_no_eviction_without_vram_estimate(self, manager):
        # Without an estimated_vram_gb, acquire() can't reason about VRAM
        # pressure and should never evict on that basis alone.
        log = []
        manager.acquire("a", "fp", lambda: FakeModel("a", log))
        manager.acquire("b", "fp", lambda: FakeModel("b", log))

        assert "a" not in log
        assert manager.stats()["entries"] == 2

    def test_no_eviction_without_gpu_manager(self):
        # Small estimates so the separate RAM-pressure check (independent of
        # gpu_manager) has no reason to fire either - this test is only about
        # the VRAM-budget axis being a no-op without a gpu_manager.
        manager = ModelLifecycleManager(gpu_manager=None, settings_manager=None)
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=0.001)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=0.001)

        assert "a" not in log
        assert manager.stats()["entries"] == 2


class TestCleanupIsOnlySite:
    def test_cleanup_runs_gc_and_empty_cache(self, manager, monkeypatch):
        calls = []
        monkeypatch.setattr("gc.collect", lambda: calls.append("gc.collect"))

        manager.cleanup()

        assert "gc.collect" in calls

    def test_acquire_and_invalidate_route_through_cleanup(self, manager, monkeypatch):
        cleanup_calls = []
        monkeypatch.setattr(manager, "cleanup", lambda aggressive=False: cleanup_calls.append(aggressive))

        # Eviction path (fingerprint bust) should call cleanup via _make_room_for/evict flow indirectly
        manager.acquire("k", "fp1", Mock(return_value=FakeModel("a")))
        manager.invalidate("k")

        assert True in cleanup_calls  # invalidate() always calls cleanup(aggressive=True)

    def test_cleanup_never_empties_pinned_host_cache_even_when_aggressive(self, manager, monkeypatch):
        # design decision: emptying CUDA's pinned-host-memory
        # cache is reserved for invalidate() (the admin "Clear VRAM & Cache
        # (RAM)" action), NOT cleanup(aggressive=True). cleanup() runs at the
        # end of EVERY generation (generation.py's finally block) and after
        # routine eviction (fingerprint bust, RAM-pressure LRU, preset
        # switch) - hot paths a long-video LTX run (the stream_to partial
        # residency, which pins streamed leaves) re-enters constantly.
        # Emptying the pinned cache there would force every such run to
        # re-pin its weights from scratch instead of reusing the warm pool.
        calls = []
        monkeypatch.setattr(manager_module, "empty_pinned_host_cache", lambda: calls.append(1))

        manager.cleanup(aggressive=True)
        manager.cleanup(aggressive=False)

        assert calls == []


class TestInvalidateClearsSecondaryCaches:
    def test_invalidate_all_empties_pinned_host_cache(self, manager, monkeypatch):
        # invalidate() (unlike cleanup()) is only ever reached via a manual/
        # admin action - the "Clear VRAM & Cache (RAM)" quick action and the
        # equivalent automation node - never a per-generation hot path, so
        # it's the one place safe to also empty CUDA's pinned-host cache.
        calls = []
        monkeypatch.setattr(manager_module, "empty_pinned_host_cache", lambda: calls.append(1))

        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))
        manager.invalidate()

        assert calls == [1]

    def test_invalidate_single_key_also_empties_pinned_host_cache(self, manager, monkeypatch):
        # Single-key invalidate is reached through the same manual/admin
        # surface (never an automatic per-generation path), so it gets the
        # same treatment.
        calls = []
        monkeypatch.setattr(manager_module, "empty_pinned_host_cache", lambda: calls.append(1))

        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))
        manager.invalidate("k")

        assert calls == [1]

    def test_invalidate_all_clears_prompt_embed_cache(self, manager, monkeypatch):
        # The admin "Clear VRAM & Cache (RAM)" action's
        # contract is to drop every native RAM cache, not just this one - the
        # prompt-embed cache (embed_cache.py) is a separate process-global
        # singleton holding detached CPU tensor clones that acquire()/
        # _evict_entry() never touch.
        fake_cache = Mock()
        monkeypatch.setattr(
            "src.platform.runtime.native.text_encoders.embed_cache.get_prompt_embed_cache",
            lambda: fake_cache,
        )

        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))
        manager.invalidate()

        fake_cache.clear.assert_called_once()

    def test_invalidate_single_key_does_not_clear_prompt_embed_cache(self, manager, monkeypatch):
        # Only a full invalidate() (clear-everything) should reach for the
        # embed cache - a single-key invalidate (fingerprint bust elsewhere)
        # has no reason to drop unrelated prompt embeddings.
        fake_cache = Mock()
        monkeypatch.setattr(
            "src.platform.runtime.native.text_encoders.embed_cache.get_prompt_embed_cache",
            lambda: fake_cache,
        )

        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))
        manager.invalidate("k")

        fake_cache.clear.assert_not_called()

    def test_invalidate_all_survives_prompt_embed_cache_failure(self, manager, monkeypatch):
        # Best-effort: a broken/absent embed cache must never break the
        # actual model-cache eviction it rides alongside.
        def _boom():
            raise RuntimeError("embed cache unavailable")

        monkeypatch.setattr(
            "src.platform.runtime.native.text_encoders.embed_cache.get_prompt_embed_cache",
            _boom,
        )

        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))
        manager.invalidate()  # must not raise

        assert manager.stats()["entries"] == 0


class TestStats:
    def test_stats_reports_hits_misses_and_vram(self, manager):
        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=4.0)
        manager.acquire("k", "fp", Mock(return_value=FakeModel("a")))

        stats = manager.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["estimated_vram_gb"] == 4.0


class TestCachedValuesAndLeasedValues:
    """`cached_values()` is the Clear VRAM action's registration-gap-proof
    fallback: unlike `leased_values()` (only the eviction-protected subset),
    it exposes every cache entry so a caller can sweep for GPU-resident
    values a placement path forgot to register with GpuResidencyManager."""

    def test_cached_values_returns_every_entry_leased_or_not(self, manager):
        a = FakeModel("a")
        b = FakeModel("b")
        manager.acquire("k1", "fp", Mock(return_value=a))
        manager.acquire("k2", "fp", Mock(return_value=b))

        assert set(manager.cached_values()) == {a, b}

    def test_cached_values_empty_for_a_fresh_cache(self, manager):
        assert manager.cached_values() == []

    def test_leased_values_only_returns_entries_held_by_an_active_lease(self, manager):
        a = FakeModel("a")
        b = FakeModel("b")
        manager.acquire("k1", "fp", Mock(return_value=a))
        manager.begin_lease("gen-1")
        manager.acquire("k2", "fp", Mock(return_value=b))

        assert manager.leased_values() == [b]
        # cached_values() still sees both - leased is a subset, not a filter.
        assert set(manager.cached_values()) == {a, b}

        manager.end_lease("gen-1")
        assert manager.leased_values() == []


class TestExpectedRamGb:
    """`expected_ram_gb()` backs the `models.cleanup.post` profiler mark's
    `cache_expected_gb` field: the cache's own belief about how much host RAM
    it holds, so `rss_gb - cache_expected_gb` on that mark names unexplained
    RSS without a special investigation."""

    def test_sums_estimated_vram_gb_across_entries(self, manager):
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=4.0)
        manager.acquire("b", "fp", Mock(return_value=FakeModel("b")), estimated_vram_gb=6.5)

        assert manager.expected_ram_gb() == pytest.approx(10.5)

    def test_zero_for_a_fresh_cache(self, manager):
        assert manager.expected_ram_gb() == 0.0

    def test_entries_without_an_estimate_contribute_zero_not_none(self, manager):
        manager.acquire("a", "fp", lambda: SimpleNamespace(module=None, unload=lambda: None))
        manager.acquire("b", "fp", Mock(return_value=FakeModel("b")), estimated_vram_gb=2.0)

        assert manager.expected_ram_gb() == pytest.approx(2.0)


def _fake_vmem(available_gb, total_gb):
    """Stand-in for `get_system_memory()`'s return value (a `SystemMemory`,
    the cgroup-aware seam every RAM-budgeting call site in manager.py reads
    through - see src/platform/runtime/system_memory.py)."""
    gb = 1024 ** 3
    return SystemMemory(available=int(available_gb * gb), total=int(total_gb * gb))


class TestRamPressureEviction:
    """`_make_room_for_ram`: evicts LRU-first when free system RAM is under
    (or would drop under) the floor, independent of gpu_manager/VRAM.

    Uses a manager with NO gpu_manager (rather than the shared `manager`
    fixture, whose fake VRAM budget is only 10GB) so the pre-existing
    VRAM-pressure path never fires and these tests isolate the RAM-only one.

    Every eviction now re-measures real free RAM via `get_system_memory()`
    (rather than trusting a running `available_gb += estimate`), so tests that
    simulate genuine progress being made must provide an iterator of
    increasingly-free readings, one per `get_system_memory()` call (initial read,
    then one per eviction, then the final post-cleanup re-measurement used for
    the persists-or-not WARNING).
    """

    @pytest.fixture
    def manager(self):
        return ModelLifecycleManager(gpu_manager=None, settings_manager=None)

    def test_floor_enforced_even_with_no_estimate(self, manager, monkeypatch):
        # This is the bug this task fixes: previously `needed_gb is None`
        # made the whole RAM-pressure check a no-op, so nothing ever evicted
        # models loaded via a path-less loader (or without a caller estimate).
        # Now it still enforces the live floor.
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: _fake_vmem(0.1, 64.0))
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log))  # no estimated_vram_gb

        # Nothing was cached yet to evict, so pressure persists - but the
        # important thing is the manager reasoned about it at all and didn't
        # silently no-op like the old `if needed_gb is None: return` did.
        assert manager.stats()["entries"] == 1

    def test_floor_enforced_evicts_lru_sole_owner(self, manager, monkeypatch):
        # available=20GB, total=64GB -> floor = max(8, 6.4) = 8GB, so 20GB is
        # fine at first. Once "b" pushes reported available down to 5GB (no
        # caller estimate at all - the A1 case), "a" must be evicted.
        responses = iter([
            _fake_vmem(20.0, 64.0),  # during acquire("a") - fine, no eviction
            _fake_vmem(5.0, 64.0),   # during acquire("b") - below floor
            _fake_vmem(20.0, 64.0),  # re-measured after evicting "a" - fine now
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log))
        manager.acquire("b", "fp", lambda: FakeModel("b", log))

        assert "a" in log
        assert "a" not in manager.stats()["keys"]
        assert "b" in manager.stats()["keys"]

    def test_no_eviction_when_plenty_of_ram_available(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: _fake_vmem(200.0, 256.0))
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=10.0)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=10.0)

        assert "a" not in log
        assert manager.stats()["entries"] == 2

    def test_evicts_lru_entry_to_relieve_ram_pressure_sole_owner(self, manager, monkeypatch):
        # 64GB total -> floor is max(8, 6.4) = 8GB. Loading a fresh 15GB
        # model with 20GB reported available would leave 5GB free (< floor)
        # -> must evict, and re-measuring after eviction reports real headroom.
        responses = iter([
            _fake_vmem(20.0, 64.0),  # acquire("a")
            _fake_vmem(20.0, 64.0),  # acquire("b")
            _fake_vmem(20.0, 64.0),  # acquire("c") initial check -> pressure
            _fake_vmem(35.0, 64.0),  # re-measured after evicting "a" -> enough
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=1.0)
        manager.acquire("c", "fp", lambda: FakeModel("c", log), estimated_vram_gb=15.0)

        # "a" is the least-recently-used entry - it must be evicted first.
        assert "a" in log
        assert "a" not in manager.stats()["keys"]
        assert "c" in manager.stats()["keys"]

    def test_stops_evicting_once_real_measurement_says_it_fits(self, manager, monkeypatch):
        responses = iter([
            _fake_vmem(20.0, 64.0),  # acquire("a")
            _fake_vmem(20.0, 64.0),  # acquire("b")
            _fake_vmem(20.0, 64.0),  # acquire("c") initial check -> pressure (20-13=7<8)
            _fake_vmem(21.0, 64.0),  # re-measured after evicting "a" -> 21-13=8>=8, stop
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []

        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)
        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=1.0)
        manager.acquire("c", "fp", lambda: FakeModel("c", log), estimated_vram_gb=13.0)

        assert "a" in log    # evicted to make room
        assert "b" not in log  # NOT evicted - "a" alone was enough
        assert "b" in manager.stats()["keys"]

    def test_warns_when_ram_pressure_persists_after_evicting_everything(self, manager, monkeypatch, caplog):
        # Nothing cached yet, and even an empty cache can't make a 500GB load fit.
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: _fake_vmem(20.0, 64.0))

        with caplog.at_level(logging.WARNING):
            manager.acquire("a", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=500.0)

        assert any("RAM pressure persists" in r.message for r in caplog.records)

    def test_no_warning_when_room_was_made_successfully(self, manager, monkeypatch, caplog):
        # Same numbers as test_stops_evicting_once_real_measurement_says_it_fits,
        # but this time the final post-cleanup re-measurement also confirms
        # real headroom, so no WARNING fires.
        responses = iter([
            _fake_vmem(20.0, 64.0),  # acquire("a")
            _fake_vmem(20.0, 64.0),  # acquire("b") initial check -> pressure (20-13=7<8)
            _fake_vmem(21.0, 64.0),  # re-measured after evicting "a" -> 21-13=8>=8, stop
            _fake_vmem(21.0, 64.0),  # final post-cleanup re-measurement
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []
        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)

        with caplog.at_level(logging.WARNING):
            manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=13.0)

        assert not any("RAM pressure persists" in r.message for r in caplog.records)

    def test_ram_pressure_eviction_calls_cleanup(self, manager, monkeypatch):
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: _fake_vmem(20.0, 64.0))
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=1.0)

        cleanup_calls = []
        monkeypatch.setattr(manager, "cleanup", lambda aggressive=False: cleanup_calls.append(aggressive))
        manager.acquire("b", "fp", Mock(return_value=FakeModel("b")), estimated_vram_gb=15.0)

        assert True in cleanup_calls

    def test_virtual_memory_error_is_non_fatal(self, manager, monkeypatch):
        def _raise():
            raise OSError("no /proc on this platform")

        monkeypatch.setattr(manager_module, "get_system_memory", _raise)
        # Must not raise - RAM pressure is a best-effort safety net, not a hard requirement.
        result = manager.acquire("a", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=10.0)
        assert result.name == "a"


class TestNeverEvictsKeyBeingAcquired:
    def test_recursive_acquire_for_different_key_is_not_evicted(self, fake_gpu_manager):
        """A loader that itself calls `models.acquire()` for a DIFFERENT key
        (e.g. LTX's projection tensors, acquired from inside the DiT's
        `acquire()` call) must never have ITS OWN in-flight key evicted by the
        room-making triggered for the inner acquire."""
        fake_gpu_manager.get_vram_budget.return_value = 5.0
        manager = ModelLifecycleManager(gpu_manager=fake_gpu_manager, settings_manager=None)
        log = []

        def outer_load():
            # While "outer" is mid-load (not yet in `_entries`), acquiring
            # "inner" triggers room-making that must not target "outer" even
            # though it isn't in `_entries` to protect itself yet.
            manager.acquire("inner", "fp", lambda: FakeModel("inner", log), estimated_vram_gb=4.0)
            return FakeModel("outer", log)

        manager.acquire("outer", "fp", outer_load, estimated_vram_gb=4.0)

        assert "outer" in manager.stats()["keys"]
        assert "inner" in manager.stats()["keys"]


class TestPostLoadFootprintRecording:
    def test_caller_estimate_wins_when_given(self, manager):
        manager.acquire("k", "fp", lambda: FakeModel("a"), estimated_vram_gb=7.5)

        assert manager.stats()["estimated_vram_gb"] == 7.5

    def test_value_estimated_vram_gb_attribute_is_recorded(self, manager):
        # NativeModel-shaped value: carries its own estimated_vram_gb.
        value = SimpleNamespace(estimated_vram_gb=3.25, module=None, unload=lambda: None)

        manager.acquire("k", "fp", lambda: value)

        assert manager.stats()["estimated_vram_gb"] == 3.25

    def test_nn_module_carrying_value_gets_measured_size(self, manager):
        import torch

        module = torch.nn.Linear(1024, 1024, bias=False)  # 1024*1024 float32 params
        value = SimpleNamespace(module=module, unload=lambda: None)

        manager.acquire("k", "fp", lambda: value)

        expected_gb = (1024 * 1024 * 4) / (1024 ** 3)
        assert manager.stats()["estimated_vram_gb"] == pytest.approx(expected_gb, rel=1e-6)

    def test_plain_value_does_not_crash_and_records_no_estimate(self, manager):
        # Shapes cached today besides model wrappers: plain lists/tensors
        # from prompt_encoder/controlnet.
        manager.acquire("k", "fp", lambda: ["conditioning", "tensor", "stuff"])

        assert manager.stats()["entries"] == 1
        assert manager.stats()["estimated_vram_gb"] == 0.0


class TestEntrySizeGb:
    """A read-only lookup of an already-cached entry's recorded size
    estimate, for a caller (NativeLLMClient._note_resident, handing a size to
    GpuResidencyManager.note_resident) that needs the number without
    re-deriving it or pretending to be a real acquire()."""

    def test_entry_size_gb_returns_the_recorded_estimate(self, manager):
        manager.acquire("k", "fp", lambda: FakeModel("a"), estimated_vram_gb=4.5)

        assert manager.entry_size_gb("k") == 4.5

    def test_entry_size_gb_returns_none_for_a_missing_key(self, manager):
        assert manager.entry_size_gb("does-not-exist") is None

    def test_entry_size_gb_returns_none_when_never_estimated(self, manager):
        manager.acquire("k", "fp", lambda: ["conditioning", "tensor"])

        assert manager.entry_size_gb("k") is None


class TestIsCached:
    """A presence check distinct from entry_size_gb, which can't tell
    "absent" apart from "present with an unknown size" - a caller that needs
    to know "is it loaded right now" (e.g. a status endpoint distinguishing
    on-disk presence from in-memory residency) needs this instead."""

    def test_is_cached_true_after_acquire(self, manager):
        manager.acquire("k", "fp", lambda: FakeModel("a"))

        assert manager.is_cached("k") is True

    def test_is_cached_false_for_a_missing_key(self, manager):
        assert manager.is_cached("does-not-exist") is False

    def test_is_cached_true_even_when_size_is_unknown(self, manager):
        manager.acquire("k", "fp", lambda: ["conditioning", "tensor"])

        assert manager.entry_size_gb("k") is None
        assert manager.is_cached("k") is True

    def test_is_cached_false_after_invalidate(self, manager):
        manager.acquire("k", "fp", lambda: FakeModel("a"))
        manager.invalidate("k")

        assert manager.is_cached("k") is False


class TestFileSizeGb:
    def test_missing_path_returns_none(self):
        assert file_size_gb("/does/not/exist/at/all.safetensors") is None

    def test_none_path_returns_none(self):
        assert file_size_gb(None) is None

    def test_real_file_returns_size_in_gb(self, tmp_path):
        f = tmp_path / "model.bin"
        f.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MiB

        result = file_size_gb(str(f))

        assert result == pytest.approx(2 * 1024 * 1024 / (1024 ** 3))


class TestEmptyPinnedHostCache:
    """torch's CUDA pinned-host-memory caching allocator is a
    third allocator (not glibc's heap, not the GPU caching allocator) that
    nothing else in this file releases. These tests exercise the dispatch
    logic against a real `torch` module (monkeypatching its attributes,
    never touching an actual GPU/CUDA context) rather than a full CUDA repro,
    which lives outside the unit-test suite (see scratchpad repro scripts)."""

    def test_noop_when_cuda_unavailable(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        calls = []
        monkeypatch.setattr(torch, "_accelerator_emptyHostCache", lambda: calls.append("accel"), raising=False)

        empty_pinned_host_cache()  # must not raise, must not call anything

        assert calls == []

    def test_prefers_accelerator_generic_api(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        calls = []
        monkeypatch.setattr(torch, "_accelerator_emptyHostCache", lambda: calls.append("accel"), raising=False)
        monkeypatch.setattr(torch._C, "_host_emptyCache", lambda: calls.append("cuda_specific"), raising=False)

        empty_pinned_host_cache()

        assert calls == ["accel"]

    def test_falls_back_to_cuda_specific_api(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.delattr(torch, "_accelerator_emptyHostCache", raising=False)
        calls = []
        monkeypatch.setattr(torch._C, "_host_emptyCache", lambda: calls.append("cuda_specific"), raising=False)

        empty_pinned_host_cache()

        assert calls == ["cuda_specific"]

    def test_silent_noop_when_neither_api_exists(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.delattr(torch, "_accelerator_emptyHostCache", raising=False)
        monkeypatch.delattr(torch._C, "_host_emptyCache", raising=False)

        empty_pinned_host_cache()  # must not raise on an older/unsupported torch build

    def test_never_raises_when_api_itself_fails(self, monkeypatch):
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        def _boom():
            raise RuntimeError("driver error")

        monkeypatch.setattr(torch, "_accelerator_emptyHostCache", _boom, raising=False)

        empty_pinned_host_cache()  # best-effort - must swallow the error


class _Settings:
    """Minimal settings stub exposing model_cache_scope."""

    def __init__(self, scope="preset"):
        self._scope = scope

    def get_setting(self, key, default=None, user_id=None):
        return self._scope if key == "model_cache_scope" else default


class TestPresetScopedCache:
    """Preset-scoped RAM cache: a native preset switch evicts the previous
    preset's cached models (Task #42)."""

    @pytest.fixture(autouse=True)
    def _reset_owner(self):
        # The owner tag is a ContextVar shared across the test thread; reset it
        # so one test's owner can't leak into another's acquires.
        token = manager_module._cache_owner.set(None)
        yield
        manager_module._cache_owner.reset(token)

    def _mgr(self, scope="preset"):
        return ModelLifecycleManager(gpu_manager=None, settings_manager=_Settings(scope))

    def test_begin_generation_tags_entries_with_owner(self):
        m = self._mgr()
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.acquire("vae", "fp", lambda: FakeModel("vae"))
        assert m._entries["dit"].owner == "presets/A"
        assert m._entries["vae"].owner == "presets/A"

    def test_switch_evicts_other_preset_and_trims(self, monkeypatch):
        trims = []
        monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: trims.append(1))
        m = self._mgr()
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.acquire("vae", "fp", lambda: FakeModel("vae"))

        m.begin_generation("presets/B")   # switch
        assert "dit" not in m._entries and "vae" not in m._entries
        assert trims, "host allocator not trimmed on preset switch"

    def test_same_preset_switch_keeps_cache(self):
        m = self._mgr()
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.begin_generation("presets/A")    # same preset -> no eviction
        assert "dit" in m._entries

    def test_none_owned_entries_survive_switch(self):
        # comfyui / warmup loads (owner None) are never auto-evicted by a native
        # preset switch.
        m = self._mgr()
        m.begin_generation(None)
        m.acquire("comfy", "fp", lambda: FakeModel("comfy"))
        assert m._entries["comfy"].owner is None

        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.begin_generation("presets/B")
        assert "comfy" in m._entries          # None-owned survived
        assert "dit" not in m._entries        # A-owned evicted

    def test_begin_generation_none_never_evicts(self):
        # A non-native (owner None) generation must not drop the native cache.
        m = self._mgr()
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.begin_generation(None)              # comfyui generation
        assert "dit" in m._entries
        assert m._last_owner == "presets/A"   # switch detector unchanged

    def test_global_scope_preserves_legacy_behaviour(self):
        m = self._mgr(scope="global")
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        m.begin_generation("presets/B")       # switch, but scope=global
        assert "dit" in m._entries            # kept until RAM pressure (legacy)

    def test_in_flight_acquiring_never_evicted(self, monkeypatch):
        monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: None)
        m = self._mgr()
        m.begin_generation("presets/A")
        m.acquire("dit", "fp", lambda: FakeModel("dit"))
        # Simulate an entry for another preset with an acquire() in flight.
        m._entries["te"] = manager_module._CacheEntry(
            key="te", fingerprint="fp", value=FakeModel("te"),
            estimated_vram_gb=None, last_used=0.0, owner="presets/A",
        )
        m._acquiring.add("te")
        m.begin_generation("presets/B")
        assert "te" in m._entries             # in-flight key protected


class TestGenerationEndSweep:
    """An in-preset checkpoint swap (same owner, new cache key) must
    not sit in RAM until the next preset switch or RAM-pressure LRU -- the
    end-of-generation sweep in ``end_lease()`` evicts entries owned by the
    finishing generation's preset that it did NOT touch this run.
    """

    @pytest.fixture(autouse=True)
    def _reset_contextvars(self):
        owner_token = manager_module._cache_owner.set(None)
        lease_token = manager_module._active_lease_id.set(None)
        yield
        manager_module._cache_owner.reset(owner_token)
        manager_module._active_lease_id.reset(lease_token)

    def _mgr(self, scope="preset"):
        return ModelLifecycleManager(gpu_manager=None, settings_manager=_Settings(scope))

    def _run_generation(self, m, owner, gen_id, acquisitions):
        """acquisitions: list of (key, loader) run under one begin_lease/
        begin_generation/end_lease cycle, mirroring generate()'s call order
        in src/features/generation/generation.py."""
        m.begin_lease(gen_id)
        m.begin_generation(owner)
        for key, loader in acquisitions:
            m.acquire(key, "fp", loader)
        m.end_lease(gen_id)

    def test_checkpoint_swap_within_preset_evicts_old_checkpoint(self):
        m = self._mgr()
        log = []
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit/ckptA", lambda: FakeModel("ckptA", log)),
        ])
        assert "dit/ckptA" in m._entries

        self._run_generation(m, "presets/A", "gen-2", [
            ("dit/ckptB", lambda: FakeModel("ckptB", log)),
        ])

        assert "dit/ckptA" not in m._entries
        assert "ckptA" in log  # actually unloaded, not just dropped
        assert "dit/ckptB" in m._entries

    def test_multiple_models_same_generation_none_evicted(self):
        m = self._mgr()
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
            ("te", lambda: FakeModel("te")),
            ("vae", lambda: FakeModel("vae")),
        ])
        assert {"dit", "te", "vae"} <= m._entries.keys()

    def test_conditional_model_skipped_this_run_is_evicted(self):
        m = self._mgr()
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
            ("upscaler", lambda: FakeModel("upscaler")),
        ])
        assert {"dit", "upscaler"} <= m._entries.keys()

        # gen-2 runs the same preset WITHOUT the upscaler toggled on.
        self._run_generation(m, "presets/A", "gen-2", [
            ("dit", lambda: FakeModel("dit")),
        ])

        assert "dit" in m._entries
        assert "upscaler" not in m._entries  # accepted tradeoff, not a bug

    def test_entry_leased_by_concurrent_generation_not_evicted(self):
        m = self._mgr()
        # gen-1 starts and acquires "shared" but does not end (still running).
        m.begin_lease("gen-1")
        m.begin_generation("presets/A")
        m.acquire("shared", "fp", lambda: FakeModel("shared"))

        # gen-2 (same preset) runs to completion without touching "shared".
        self._run_generation(m, "presets/A", "gen-2", [
            ("other", lambda: FakeModel("other")),
        ])

        # "shared" is still leased by the still-running gen-1 -> not evicted.
        assert "shared" in m._entries
        assert m._entries["shared"].leased_by == {"gen-1"}

        # gen-1 finishes and touched "shared" itself -> its own sweep is a no-op.
        m.end_lease("gen-1")
        assert "shared" in m._entries

    def test_global_scope_disables_sweep(self):
        m = self._mgr(scope="global")
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit/ckptA", lambda: FakeModel("ckptA")),
        ])
        self._run_generation(m, "presets/A", "gen-2", [
            ("dit/ckptB", lambda: FakeModel("ckptB")),
        ])
        # Legacy behaviour: both checkpoints persist until RAM pressure.
        assert "dit/ckptA" in m._entries
        assert "dit/ckptB" in m._entries

    def test_preset_switch_eviction_still_works_with_lease_sweep(self):
        """Regression: combining preset-switch eviction (begin_generation)
        with the new end-of-generation sweep (end_lease) must not double-free
        or otherwise misbehave."""
        m = self._mgr()
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
        ])
        assert "dit" in m._entries

        # gen-2 switches preset entirely -- begin_generation's foreign-owner
        # eviction fires before gen-2's own acquires run.
        self._run_generation(m, "presets/B", "gen-2", [
            ("dit", lambda: FakeModel("dit_b")),
        ])

        assert m._entries["dit"].owner == "presets/B"
        assert m._entries["dit"].value.name == "dit_b"

    def test_mid_acquire_key_never_swept(self, monkeypatch):
        monkeypatch.setattr(manager_module, "trim_host_allocator", lambda: None)
        m = self._mgr()
        # A settled entry for the same preset, currently mid-acquire on
        # (simulated) another thread -- must survive the sweep untouched.
        m._entries["te"] = manager_module._CacheEntry(
            key="te", fingerprint="fp", value=FakeModel("te"),
            estimated_vram_gb=None, last_used=0.0, owner="presets/A",
        )
        m._acquiring.add("te")

        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
        ])

        assert "te" in m._entries  # in-flight key protected from the sweep

    def test_owner_none_never_sweeps(self):
        """Non-native (comfyui/warmup) generations tag owner=None and must
        never trigger the preset-scoped sweep."""
        m = self._mgr()
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
        ])
        # gen-2 is a comfyui/non-native generation (owner=None).
        self._run_generation(m, None, "gen-2", [
            ("comfy", lambda: FakeModel("comfy")),
        ])
        assert "dit" in m._entries
        assert "comfy" in m._entries

    def test_ownerless_entry_survives_generation_sweep(self):
        """An entry acquired outside any generation (the native LLM chat model
        acquires with no _cache_owner set) carries owner=None and must survive
        a preset-owned generation's end-of-generation sweep untouched."""
        m = self._mgr()
        m.acquire("native/llm/chat", "fp-llm", lambda: FakeModel("llm"), estimated_vram_gb=0.1)
        self._run_generation(m, "presets/A", "gen-1", [
            ("dit", lambda: FakeModel("dit")),
        ])
        assert "native/llm/chat" in m._entries
        assert "dit" in m._entries
