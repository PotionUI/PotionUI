"""A tiny in-process TTL cache.

PotionUI's backend is single-process (see CLAUDE.md), so a plain dict with a
per-entry expiry timestamp is all the chat/LLM hot path needs to avoid
re-fetching the same DB row or rebuilding the same prompt/tool text on every
message. Not thread-safe beyond what the GIL already gives dict operations —
fine for the asyncio single-process usage here; every caller (chat context
builder, ollama wire, LLM repository) runs on the event loop's single thread,
so no lock is taken. Do not share an instance across threads.

Bounded on two axes so a long-lived process can't grow this without limit:

- ``max_entries`` caps live entries with least-recently-used eviction on
  ``set``.
- ``_expiry`` (key -> expires_at, in set()/refresh order) mirrors ``_entries``
  exactly: every place a key leaves ``_entries`` — LRU eviction, an
  expired-on-read pop, ``invalidate``, ``clear`` — removes it from ``_expiry``
  in the same step, and ``set`` re-homes a key's ``_expiry`` slot to the end.
  Invariant: ``_expiry`` holds exactly one record per key currently in
  ``_entries``, so its size never exceeds ``max_entries`` no matter the access
  pattern — earlier code let a long-lived head entry block a FIFO queue's
  early-exit sweep, so unrelated evicted/overwritten keys behind it in the
  queue piled up unboundedly instead of being bounded by ``max_entries``.
  On top of that structural bound, every ``get``/``set`` also runs a
  fixed-size proactive sweep (at most ``sweep_budget`` entries,
  oldest-expiry-first) that reclaims a key's value once its ttl passes even
  when nobody reads that exact key again and capacity is never hit.
"""

import time
from collections import OrderedDict
from typing import Callable, Generic, Optional, Tuple, TypeVar

K = TypeVar("K")
V = TypeVar("V")

_DEFAULT_MAX_ENTRIES = 256
_DEFAULT_SWEEP_BUDGET = 8


class TTLCache(Generic[K, V]):
    """Maps a key to a (value, expires_at) pair; entries expire after ``ttl_seconds``."""

    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        sweep_budget: int = _DEFAULT_SWEEP_BUDGET,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._sweep_budget = sweep_budget
        # None (the default for every current caller) means "read time.monotonic
        # fresh on each call", matching the pre-existing inline `time.monotonic()`
        # so tests that patch that module attribute keep working unchanged; an
        # injected clock is for tests that want to advance time without sleeping.
        self._clock = clock
        # LRU-recency order (touched by get/set via move_to_end).
        self._entries: "OrderedDict[K, Tuple[V, float]]" = OrderedDict()
        # Expiry order (touched only by set/removal, never by a bare get) —
        # see the class docstring's invariant: always the same key set as
        # `_entries`, so always the same size, always <= max_entries.
        self._expiry: "OrderedDict[K, float]" = OrderedDict()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def _drop(self, key: K) -> None:
        self._entries.pop(key, None)
        self._expiry.pop(key, None)

    def _sweep_expired(self) -> None:
        now = self._now()
        for _ in range(self._sweep_budget):
            if not self._expiry:
                return
            key, expires_at = next(iter(self._expiry.items()))
            if now < expires_at:
                return  # oldest-by-expiry entry is still live -> nothing older to reclaim
            self._drop(key)

    def get(self, key: K) -> Optional[V]:
        self._sweep_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._now() >= expires_at:
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._sweep_expired()
        expires_at = self._now() + self._ttl
        self._entries[key] = (value, expires_at)
        self._entries.move_to_end(key)
        self._expiry.pop(key, None)
        self._expiry[key] = expires_at
        while len(self._entries) > self._max_entries:
            lru_key, _ = self._entries.popitem(last=False)
            self._expiry.pop(lru_key, None)

    def invalidate(self, key: K) -> None:
        self._drop(key)

    def clear(self) -> None:
        self._entries.clear()
        self._expiry.clear()
