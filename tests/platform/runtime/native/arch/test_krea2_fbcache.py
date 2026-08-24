"""FBCache integration on the Krea-2 arch (seam lives in run_blocks)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_krea2_model import TINY, _build_ready

_T = torch.tensor([0.5])
_CFG2 = dict(TINY, layers=2)  # >=2 blocks so "later blocks avoided" is meaningful


def _ctx():
    return torch.randn(1, 5, 3, 16)


def _fwd(m, x, ctx, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, attention_mask=torch.ones(1, 5, dtype=torch.long), **kw)


def test_step_cache_none_is_byte_identical():
    m = _build_ready(_CFG2)
    x = torch.randn(1, 4, 8, 8)
    ctx = _ctx()
    base = _fwd(m, x, ctx)
    assert torch.equal(base, _fwd(m, x, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build_ready(_CFG2)
    x = torch.randn(1, 4, 8, 8)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_different_inputs_do_not_skip():
    m = _build_ready(_CFG2)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 4, 8, 8), ctx, step_cache=cache)
    _fwd(m, torch.randn(1, 4, 8, 8) * 5.0, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build_ready(_CFG2)
    x = torch.randn(1, 4, 8, 8)
    ctx = _ctx()
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


def test_resolution_change_forces_compute():
    m = _build_ready(_CFG2)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 4, 8, 8), ctx, step_cache=cache)
    out = _fwd(m, torch.randn(1, 4, 8, 16), ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape == (1, 4, 8, 16)
