# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: common/cache.py @ unknown; vendored ~2025 (moved into
# vendor/seedvr2/ from src/platform/runtime/native/arch/seedvr2/ as part of
# the license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""Per-forward memoization cache — faithful port of SeedVR2's ``common.cache.Cache``.

The NaDiT reference threads one ``Cache`` through the whole forward pass. Most of
what it stores is a pure optimization (window partition indices, RoPE freqs,
varlen ``cu_seqlens``): recomputing those from the same shapes yields identical
values, so dropping the cache would still be correct.

**One entry is load-bearing and must be preserved:** ``vid_out_ada`` calls the
final adaLN with ``layers=["out"]`` — which, computed fresh, would split the
15360-wide timestep embedding into 5120-wide groups (wrong: the video width is
2560). The reference never computes it fresh: block 0's video adaLN has already
cached ``emb_repeat_0_vid`` (the 2560-wide *attn* modulation split, ``layers=
["attn","mlp"]``), and the final adaLN's ``(idx=0, branch_tag="vid")`` key
collides with it, so it silently reuses that 2560-wide split. The result is that
the output norm is modulated by the block-0 attn shift/scale plus its own learned
``out_shift``/``out_scale`` params. Reproducing this collision requires the real
cache, so this is a faithful (not simplified) reimplementation.

``namespace`` returns a view that prefixes keys but shares the underlying store,
exactly like the reference (so a namespaced writer and a later namespaced reader
see the same entry).
"""

from __future__ import annotations

from typing import Any, Callable, Optional


class Cache:
    def __init__(self, disable: bool = False, prefix: str = "", store: Optional[dict] = None) -> None:
        self.disable = disable
        self.prefix = prefix
        self.store = store if store is not None else {}

    def __call__(self, key: str, fn: Callable[[], Any]) -> Any:
        if self.disable:
            return fn()
        full = self.prefix + key
        if full not in self.store:
            self.store[full] = fn()
        return self.store[full]

    def get(self, key: str) -> Any:
        return self.store.get(self.prefix + key)

    def namespace(self, name: str) -> "Cache":
        return Cache(disable=self.disable, prefix=f"{self.prefix}{name}.", store=self.store)
