"""Tests for the bounded TTL cache (LLM-02): capacity, reclamation, freshness."""

from src.features.llm.ttl_cache import TTLCache


class FakeClock:
    """A deterministic, manually-advanced clock for the injected `clock` seam."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestCapacity:
    def test_many_unique_keys_stay_bounded_at_capacity(self):
        clock = FakeClock()
        cache: TTLCache[int, int] = TTLCache(ttl_seconds=1000.0, max_entries=10, clock=clock)

        for i in range(1000):
            cache.set(i, i * i)

        assert len(cache._entries) <= 10

    def test_capacity_eviction_is_least_recently_used(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=1000.0, max_entries=3, clock=clock)

        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        # touch "a" so it is no longer the least-recently-used entry
        assert cache.get("a") == "1"
        cache.set("d", "4")  # should evict "b", the true LRU entry

        assert cache.get("b") is None
        assert "b" not in cache._entries
        assert cache.get("a") == "1"
        assert cache.get("c") == "3"
        assert cache.get("d") == "4"


class TestExpiryReclamation:
    def test_expired_never_read_keys_are_reclaimed_by_later_unrelated_operations(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(
            ttl_seconds=10.0, max_entries=100, sweep_budget=1, clock=clock
        )

        cache.set("stale-1", "v1")
        cache.set("stale-2", "v2")
        clock.advance(11.0)  # both entries are now expired and never read again

        # unrelated operations, one sweep step (sweep_budget=1) at a time
        cache.set("unrelated-a", "x")
        assert "stale-1" not in cache._entries

        cache.set("unrelated-b", "y")
        assert "stale-2" not in cache._entries

    def test_sweep_does_not_touch_entries_that_have_not_expired(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, max_entries=100, clock=clock)

        cache.set("fresh", "v")
        clock.advance(1.0)
        cache.set("trigger-sweep", "w")

        assert "fresh" in cache._entries
        assert cache.get("fresh") == "v"

    def test_live_hit_returns_value(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, clock=clock)

        cache.set("k", "v")
        assert cache.get("k") == "v"

    def test_expired_key_read_directly_is_a_miss_and_is_removed(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, clock=clock)

        cache.set("k", "v")
        clock.advance(11.0)

        assert cache.get("k") is None
        assert "k" not in cache._entries


class TestOverwriteAndInvalidation:
    def test_overwrite_refreshes_value_and_expiry(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, clock=clock)

        cache.set("k", "v1")
        clock.advance(6.0)
        cache.set("k", "v2")  # refresh before original expiry
        clock.advance(6.0)  # 12s since first set, but only 6s since refresh

        assert cache.get("k") == "v2"

    def test_invalidate_removes_entry(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, clock=clock)

        cache.set("k", "v")
        cache.invalidate("k")

        assert cache.get("k") is None
        assert "k" not in cache._entries

    def test_clear_removes_all_entries(self):
        clock = FakeClock()
        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0, clock=clock)

        cache.set("a", "1")
        cache.set("b", "2")
        cache.clear()

        assert cache.get("a") is None
        assert cache.get("b") is None
        assert len(cache._entries) == 0


class TestDefaultClockBackwardCompatibility:
    def test_default_clock_reads_time_monotonic_live(self, monkeypatch):
        """No `clock` passed: behaves like the pre-existing inline time.monotonic()
        call, so callers patching `src.features.llm.ttl_cache.time.monotonic`
        (see test_repository_config_cache.py) keep working unchanged."""
        import time as real_time

        original_monotonic = real_time.monotonic

        cache: TTLCache[str, str] = TTLCache(ttl_seconds=10.0)
        cache.set("k", "v")

        monkeypatch.setattr(
            "src.features.llm.ttl_cache.time.monotonic",
            lambda: original_monotonic() + 3600,
        )

        assert cache.get("k") is None
