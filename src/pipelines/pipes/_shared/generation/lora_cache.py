"""Bounded RAM cache for parsed LoRA state dicts.

Nothing between the model picker and ``apply_loras`` remembered a parsed LoRA.
``load_lora_stack`` re-read every file whenever a cached DiT's stack changed,
and ``load_windowed_lora_stack`` re-read every windowed file once per
generation (it runs from the generator's ``build_context``), so a preset that
never changes its adapters still paid the parse on every run.

Identity is ``(resolved path, mtime_ns, size)``, not the path: a LoRA being
iterated on is rewritten under the same name, and serving the previous parse
for it would silently generate against the old weights. A file that cannot be
stat'd has no identity this can verify, so it is neither stored nor served —
the caller simply reads it, exactly as before this existed.

Bounded by total tensor footprint with LRU eviction. There is no cache-size
setting to read (``model_cache_scope`` is the model cache's scope, not a byte
budget) and no ``Settings`` reaches this depth, so the cap is a constant:
:data:`CAPACITY_BYTES`, sized to hold a typical multi-adapter stack plus one
swap while staying negligible next to the tens of GB the checkpoint itself
occupies. A single file larger than the cap is never admitted rather than
evicting everything else to hold it alone.

The cached dict is shared by every caller that asks for the same file, so
nothing may mutate one. ``map_lora_keys`` reads it and builds fresh tensors;
both application modes read and never write. A future consumer that wants to
edit a state dict in place must copy it first.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Total parsed-LoRA footprint held in RAM before the least recently used
#: entries are dropped.
CAPACITY_BYTES = 2 * 1024 ** 3

#: ``(resolved path, mtime_ns, size)`` — what makes a parse re-servable.
CacheKey = Tuple[str, int, int]


def file_identity(file_path: str) -> Optional[CacheKey]:
    """``file_path``'s cache identity, or None when it cannot be stat'd."""
    try:
        stat = os.stat(file_path)
    except OSError:
        return None
    return (os.path.realpath(file_path), stat.st_mtime_ns, stat.st_size)


class LoraStateDictCache:
    """LRU over parsed LoRA state dicts, bounded by their tensor footprint."""

    def __init__(self, capacity_bytes: int = CAPACITY_BYTES) -> None:
        self._capacity = int(capacity_bytes)
        self._entries: "OrderedDict[CacheKey, Tuple[Dict[str, Any], int]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_load(
        self,
        file_path: str,
        load: Callable[[], Tuple[Dict[str, Any], int]],
    ) -> Tuple[Dict[str, Any], int, bool]:
        """``(state_dict, nbytes, was_a_hit)`` for ``file_path``.

        ``load`` returns ``(state_dict, nbytes)`` and runs OUTSIDE the lock, so
        a slow read never blocks another generation's lookup. Two callers
        racing the same cold file both read it and the second insert wins;
        duplicating one read is cheaper than serialising every read.
        """
        key = file_identity(file_path)
        if key is not None:
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                    self.hits += 1
                    return entry[0], entry[1], True
            self.misses += 1
        state_dict, nbytes = load()
        if key is not None:
            self._store(key, state_dict, nbytes)
        return state_dict, nbytes, False

    def _store(self, key: CacheKey, state_dict: Dict[str, Any], nbytes: int) -> None:
        if nbytes > self._capacity:
            logger.debug("lora cache: %s (%d bytes) exceeds the cache capacity, not stored",
                         key[0], nbytes)
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            self._entries[key] = (state_dict, nbytes)
            self._bytes += nbytes
            while self._bytes > self._capacity and len(self._entries) > 1:
                _evicted_key, (_sd, evicted_bytes) = self._entries.popitem(last=False)
                self._bytes -= evicted_bytes

    @property
    def resident_bytes(self) -> int:
        return self._bytes

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self.hits = 0
            self.misses = 0


_cache = LoraStateDictCache()


def lora_state_dict_cache() -> LoraStateDictCache:
    """The process-wide parsed-LoRA cache."""
    return _cache
