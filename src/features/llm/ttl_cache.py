"""A tiny in-process TTL cache.

PotionUI's backend is single-process (see CLAUDE.md), so a plain dict with a
per-entry expiry timestamp is all the chat/LLM hot path needs to avoid
re-fetching the same DB row or rebuilding the same prompt/tool text on every
message. Not thread-safe beyond what the GIL already gives dict operations —
fine for the asyncio single-process usage here; every caller (chat context
builder, ollama wire, LLM repository) runs on the event loop's single thread,
so no lock is taken. Do not share an instance across threads.

Bounded on two axes so a long-lived process can't grow this without limit:
``max_entries`` caps live entries with least-recently-used eviction on
``set``, and every ``get``/``set`` runs a fixed-size sweep (at most
``sweep_budget`` entries, oldest-expiry-first) that reclaims expired entries
even when their key is never read again — previously an abandoned key's
value lingered until, and unless, that exact key was looked up again.
"""

import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Dict, Generic, Optional, Tuple, TypeVar

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
        self._entries: "OrderedDict[K, Tuple[V, float]]" = OrderedDict()
        # (expires_at, key) in insertion order. ttl_seconds is fixed per instance,
        # so insertion order is also expiry order, letting the sweep stop as soon
        # as it hits an entry that hasn't expired yet.
        self._expiry_order: Deque[Tuple[float, K]] = deque()

    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    def _sweep_expired(self) -> None:
        now = self._now()
        for _ in range(self._sweep_budget):
            if not self._expiry_order:
                return
            expires_at, key = self._expiry_order[0]
            entry = self._entries.get(key)
            still_current = entry is not None and entry[1] == expires_at
            if still_current and now < expires_at:
                return  # oldest queued entry is still live -> nothing older to reclaim
            self._expiry_order.popleft()
            if still_current:
                self._entries.pop(key, None)

    def get(self, key: K) -> Optional[V]:
        self._sweep_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._now() >= expires_at:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        self._sweep_expired()
        expires_at = self._now() + self._ttl
        self._entries[key] = (value, expires_at)
        self._entries.move_to_end(key)
        self._expiry_order.append((expires_at, key))
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def invalidate(self, key: K) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        self._expiry_order.clear()
