"""Tests for generation-scoped leases - the production crash fix.

Production incident (2026-07-15): an LTX generation hit RAM pressure during the
TE acquire (after the DiT loaded), evicted the DiT cache entry, and the
generator pipe's bundle.dit weakref cleared -> AttributeError on bundle.dit.spec.

The fix: models acquired during a generation are leased (unevictable) until the
generation completes, so RAM-pressure eviction can't drop a model mid-pipeline.
"""
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import src.platform.runtime.model_lifecycle.lifecycle as manager_module
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.platform.runtime.system_memory import SystemMemory


class FakeModel:
    """Stand-in for a model wrapper (see test_manager.py for full docstring)."""

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


def _fake_vmem(available_gb: float, total_gb: float):
    """Mock get_system_memory() response (see test_manager.py)."""
    return SystemMemory(available=int(available_gb * (1024 ** 3)), total=int(total_gb * (1024 ** 3)))


@pytest.fixture(autouse=True)
def _no_real_cuda_calls(monkeypatch):
    monkeypatch.setattr("torch.cuda.synchronize", lambda: None, raising=False)
    monkeypatch.setattr("torch.cuda.empty_cache", lambda: None, raising=False)
    # Clear any leaked ContextVar state from previous tests
    manager_module._active_lease_id.set(None)
    manager_module._cache_owner.set(None)


@pytest.fixture
def fake_gpu_monitor():
    gpu = Mock()
    gpu.get_vram_budget.return_value = 10.0
    return gpu


@pytest.fixture
def manager(fake_gpu_monitor):
    return ModelLifecycle(gpu_monitor=fake_gpu_monitor, settings=None)


class TestLeaseProtectsFromRAMPressureEviction:
    """The crash scenario: RAM pressure must NOT evict leased entries."""

    def test_leased_model_survives_ram_pressure(self, manager, monkeypatch):
        """Production crash flow: acquire model A under a lease, force RAM
        pressure, acquire model B -> A's entry SURVIVES and its value is still
        retrievable.
        """
        # 64GB total -> floor 8GB. Loading a 15GB model with 20GB free would
        # leave 5GB (< floor), forcing eviction. But if A is leased, only B+
        # can be evicted.
        responses = iter([
            _fake_vmem(20.0, 64.0),  # acquire("a") - fine
            _fake_vmem(20.0, 64.0),  # acquire("b") - pressure: 20-15=5 < 8
            _fake_vmem(20.0, 64.0),  # re-measured after trying to evict (nothing evictable)
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []

        # Begin a lease, acquire A under it
        manager.begin_lease("gen-1")
        model_a = manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)
        # Now acquire B with pressure - A must NOT be evicted (it's leased)
        model_b = manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=15.0)

        # Both models are in the cache
        assert "a" in manager.stats()["keys"]
        assert "b" in manager.stats()["keys"]
        # A was NOT evicted/unloaded
        assert "a" not in log
        assert model_a.module is not None
        # The lease is still tracking A
        assert manager.stats()["leased_keys"] == 2  # both a and b are leased

        # Verify we can still retrieve A (the production crash: bundle.dit was None)
        retrieved_a = manager.acquire("a", "fp", lambda: None)
        assert retrieved_a is model_a

    def test_lease_release_makes_entries_evictable_again(self, manager, monkeypatch):
        """After lease ends, same RAM pressure DOES evict the entry."""
        responses = iter([
            _fake_vmem(20.0, 64.0),  # acquire("a") under lease
            _fake_vmem(20.0, 64.0),  # acquire("b") after lease - pressure
            _fake_vmem(35.0, 64.0),  # re-measured after evicting "a"
        ])
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: next(responses))
        log = []

        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)
        manager.end_lease("gen-1")  # A becomes evictable

        manager.acquire("b", "fp", lambda: FakeModel("b", log), estimated_vram_gb=15.0)

        # Now A WAS evicted
        assert "a" in log
        assert "a" not in manager.stats()["keys"]
        assert "b" in manager.stats()["keys"]

    def test_context_manager_releases_lease_on_exception(self, manager):
        """Exception inside the lease context still releases it."""
        log = []
        manager.acquire("a", "fp", lambda: FakeModel("a", log), estimated_vram_gb=1.0)

        with pytest.raises(ValueError):
            with manager.generation_lease("gen-1"):
                manager.acquire("a", "fp", lambda: None)  # cache hit, marks as leased
                raise ValueError("simulated generation failure")

        # Lease was released despite the exception
        assert manager.stats()["active_leases"] == 0
        assert manager.stats()["leased_keys"] == 0
        # Entry exists but is no longer leased
        entry = manager._entries["a"]
        assert not entry.leased_by


class TestFingerprintBustOfLeasedEntry:
    """Fingerprint-bust (e.g. LoRA swap) must replace a leased entry but not
    unload it if still referenced.
    """

    def test_fingerprint_bust_replaces_leased_entry(self, manager):
        """Same key, new fingerprint: entry is replaced even though leased."""
        log = []
        manager.begin_lease("gen-1")
        model_a = manager.acquire("dit", "fp_no_lora", lambda: FakeModel("a", log))

        # Fingerprint bust while leased
        model_b = manager.acquire("dit", "fp_with_lora", lambda: FakeModel("b", log))

        # New entry replaces old, old value NOT unloaded (we still hold model_a)
        assert model_b.name == "b"
        assert "a" not in log
        assert model_a.module is not None
        # New entry is also leased
        assert manager.stats()["leased_keys"] == 1

    def test_fingerprint_bust_logs_leased_entry_eviction(self, manager, caplog):
        """Fingerprint-bust of a leased entry logs which leases held it."""
        manager.begin_lease("gen-1")
        manager.acquire("dit", "fp_no_lora", Mock(return_value=FakeModel("a")))

        with caplog.at_level(logging.INFO):
            manager.acquire("dit", "fp_with_lora", Mock(return_value=FakeModel("b")))

        assert any(
            "evicting LEASED entry" in r.message and "gen-1" in r.message
            for r in caplog.records
        )


class TestInvalidateOverridesLeases:
    """invalidate() (admin/manual) CAN evict leased entries but logs loudly."""

    def test_invalidate_single_leased_key_warns(self, manager, caplog):
        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))

        with caplog.at_level(logging.WARNING):
            manager.invalidate("a")

        assert any(
            "invalidate" in r.message and "LEASED" in r.message and "gen-1" in r.message
            for r in caplog.records
        )
        assert "a" not in manager.stats()["keys"]

    def test_invalidate_all_with_leased_entries_warns(self, manager, caplog):
        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))
        manager.acquire("b", "fp", Mock(return_value=FakeModel("b")))

        with caplog.at_level(logging.WARNING):
            manager.invalidate()

        assert any(
            "invalidate(all)" in r.message and "LEASED" in r.message
            for r in caplog.records
        )
        assert manager.stats()["entries"] == 0


class TestLeaseTracking:
    """Lease bookkeeping: multiple leases, acquire under no lease, etc."""

    def test_acquire_with_no_active_lease_does_not_mark_entry(self, manager):
        """No lease active -> entries behave exactly as before (not leased)."""
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))

        entry = manager._entries["a"]
        assert not entry.leased_by
        assert manager.stats()["active_leases"] == 0

    def test_multiple_concurrent_leases_track_independently(self, manager):
        """Two leases (e.g. two comfyui workers) each protect their own keys."""
        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))

        manager.begin_lease("gen-2")
        manager.acquire("b", "fp", Mock(return_value=FakeModel("b")))

        assert manager.stats()["active_leases"] == 2
        assert manager.stats()["leased_keys"] == 2
        assert manager._entries["a"].leased_by == {"gen-1"}
        assert manager._entries["b"].leased_by == {"gen-2"}

    def test_same_key_leased_by_multiple_generations(self, manager):
        """A cache hit under a different lease adds that lease to the entry."""
        manager.begin_lease("gen-1")
        manager.acquire("shared", "fp", Mock(return_value=FakeModel("shared")))

        manager.begin_lease("gen-2")
        manager.acquire("shared", "fp", Mock(return_value=FakeModel("shared")))  # hit

        # Entry is leased by BOTH generations
        assert manager._entries["shared"].leased_by == {"gen-1", "gen-2"}
        # Entry is evictable only after BOTH leases end
        manager.end_lease("gen-1")
        assert manager._entries["shared"].leased_by == {"gen-2"}
        manager.end_lease("gen-2")
        assert not manager._entries["shared"].leased_by

    def test_end_lease_twice_is_safe(self, manager):
        """Double-release (e.g. exception-handling accident) is a no-op."""
        manager.begin_lease("gen-1")
        manager.end_lease("gen-1")
        manager.end_lease("gen-1")  # no crash

    def test_end_lease_without_begin_is_safe(self, manager):
        """Release-without-begin (e.g. feature flag flipped mid-run) is a no-op."""
        manager.end_lease("never-began")  # no crash


class TestLeasedGBReporting:
    """The 'persists' warning must report how much was leased and skipped."""

    def test_ram_pressure_persists_reports_leased_gb(self, manager, monkeypatch, caplog):
        """When pressure persists, the warning names the leased GB that
        couldn't be evicted (production context: "27GB leased" directly
        explains why room-making failed).
        """
        monkeypatch.setattr(manager_module, "get_system_memory", lambda: _fake_vmem(5.0, 64.0))
        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")), estimated_vram_gb=27.0)

        with caplog.at_level(logging.WARNING):
            manager.acquire("b", "fp", Mock(return_value=FakeModel("b")), estimated_vram_gb=10.0)

        assert any(
            "RAM pressure persists" in r.message and "27" in r.message and "leased" in r.message
            for r in caplog.records
        )


class TestGenerationLeaseContextManager:
    """generation_lease() context manager (the public API for GenerationEngine)."""

    def test_context_manager_acquires_and_releases_lease(self, manager):
        log = []
        with manager.generation_lease("gen-1"):
            manager.acquire("a", "fp", lambda: FakeModel("a", log))
            assert manager.stats()["active_leases"] == 1

        assert manager.stats()["active_leases"] == 0
        assert not manager._entries["a"].leased_by

    def test_nested_contexts_for_same_generation_reuse_lease(self, manager):
        """Nested with-blocks for the SAME generation_id reuse the lease
        (begin_lease logs a warning but doesn't crash).
        """
        with manager.generation_lease("gen-1"):
            with manager.generation_lease("gen-1"):
                manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))
                assert manager.stats()["active_leases"] == 1


class TestLeaseHitMissLoadStats:
    """Per-lease hit/miss/load_ms counters -- the always-on,
    profiling-independent cold/warm signal the admin stats table relies on.
    """

    def test_all_misses_reports_cold(self, manager):
        """A lease whose every acquire() was a miss (nothing cached yet) is
        the "cold start" case: `misses > 0`."""
        with manager.generation_lease("gen-1") as lease_stats:
            manager.acquire("dit", "fp", lambda: FakeModel("dit"))

        assert lease_stats["hits"] == 0
        assert lease_stats["misses"] == 1
        assert lease_stats["load_ms"] >= 0

    def test_all_hits_reports_warm(self, manager):
        """Pre-warm the cache outside any lease, then acquire the SAME
        key+fingerprint under a lease -- every acquire is a hit, `misses == 0`
        (the "warm start" case)."""
        manager.acquire("dit", "fp", lambda: FakeModel("dit"))  # warms the cache

        with manager.generation_lease("gen-1") as lease_stats:
            manager.acquire("dit", "fp", lambda: FakeModel("dit"))  # hit

        assert lease_stats["hits"] == 1
        assert lease_stats["misses"] == 0

    def test_mixed_hit_and_miss_is_cold(self, manager):
        """One hit + one miss under the same lease still counts as cold --
        cold/warm is decided by `misses > 0`, not by majority."""
        manager.acquire("te", "fp", lambda: FakeModel("te"))  # pre-warm te only

        with manager.generation_lease("gen-1") as lease_stats:
            manager.acquire("te", "fp", lambda: FakeModel("te"))  # hit
            manager.acquire("dit", "fp", lambda: FakeModel("dit"))  # miss

        assert lease_stats["hits"] == 1
        assert lease_stats["misses"] == 1

    def test_load_ms_measures_only_the_loader_call(self, manager):
        """`load_ms` accumulates wall time spent inside `loader()` calls,
        not the whole acquire() overhead."""
        import time as time_module

        def slow_loader():
            time_module.sleep(0.02)
            return FakeModel("dit")

        with manager.generation_lease("gen-1") as lease_stats:
            manager.acquire("dit", "fp", slow_loader)

        assert lease_stats["load_ms"] >= 15  # allow scheduler slack below 20ms

    def test_acquire_with_no_active_lease_does_not_crash_or_record(self, manager):
        """acquire() outside any lease still works and simply has no lease
        counters to update (covered by the private `_lease_stats` dict
        staying empty, not observable from the public `generation_lease` API
        directly -- exercised here via the no-crash contract)."""
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))  # no crash
        assert manager._lease_stats == {}

    def test_end_lease_returns_none_for_unknown_lease(self, manager):
        assert manager.end_lease("never-began") is None

    def test_lease_stats_popped_after_end_lease(self, manager):
        """`_lease_stats` must not grow unbounded across many generations."""
        manager.begin_lease("gen-1")
        manager.acquire("a", "fp", Mock(return_value=FakeModel("a")))
        manager.end_lease("gen-1")

        assert "gen-1" not in manager._lease_stats
