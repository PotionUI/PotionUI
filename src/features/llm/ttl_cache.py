"""A tiny in-process TTL cache.

PotionUI's backend is single-process (see CLAUDE.md), so a plain dict with a
per-entry expiry timestamp is all the chat/LLM hot path needs to avoid
re-fetching the same DB row or rebuilding the same prompt/tool text on every
message. Not thread-safe beyond what the GIL already gives dict operations —
fine for the asyncio single-process usage here.
"""

import time
from typing import Any, Dict, Generic, Optional, Tuple, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Maps a key to a (value, expires_at) pair; entries expire after ``ttl_seconds``."""

    def __init__(self, ttl_seconds: float):
        self._ttl = ttl_seconds
        self._entries: Dict[K, Tuple[V, float]] = {}

    def get(self, key: K) -> Optional[V]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        return value

    def set(self, key: K, value: V) -> None:
        self._entries[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: K) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
