"""FBCache integration on the Wan arch (seam in _forward_orig; SLG interaction)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_wan_model import TINY_T2V, _build

_T = torch.tensor([0.5])


def _fwd(m, x, ctx, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, **kw)


def test_step_cache_none_is_byte_identical():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    ctx = torch.randn(1, 12, 32)
    base = _fwd(m, x, ctx)
    assert torch.equal(base, _fwd(m, x, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    ctx = torch.randn(1, 12, 32)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_different_inputs_do_not_skip():
    m = _build(TINY_T2V)
    ctx = torch.randn(1, 12, 32)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 16, 4, 16, 16), ctx, step_cache=cache)
    _fwd(m, torch.randn(1, 16, 4, 16, 16) * 5.0, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build(TINY_T2V)  # num_layers=2 -> blocks[-1] is block 1
    x = torch.randn(1, 16, 4, 16, 16)
    ctx = torch.randn(1, 12, 32)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    last = m.blocks[-1]
    orig = last.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    last.forward = counting
    _fwd(m, x, ctx, step_cache=cache)
    assert calls["n"] == 1
    _fwd(m, x, ctx, step_cache=cache)
    assert calls["n"] == 1


def test_skip_layers_pass_never_touches_cache():
    # A degraded (SLG) forward must never read or write the cache — the arch
    # guards it even when a cache is handed in alongside skip_layers.
    m = _build(TINY_T2V)
    x = torch.randn(1, 16, 4, 16, 16)
    ctx = torch.randn(1, 12, 32)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    _fwd(m, x, ctx, step_cache=cache)  # computed=1
    assert cache.stats() == {"computed": 1, "skipped": 0}
    # identical inputs would normally skip; with skip_layers set the cache is
    # bypassed entirely, so counters are unchanged.
    _fwd(m, x, ctx, step_cache=cache, skip_layers={0})
    assert cache.stats() == {"computed": 1, "skipped": 0}


def test_resolution_change_forces_compute():
    m = _build(TINY_T2V)
    ctx = torch.randn(1, 12, 32)
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 16, 4, 16, 16), ctx, step_cache=cache)
    out = _fwd(m, torch.randn(1, 16, 4, 16, 32), ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape[-1] == 32
