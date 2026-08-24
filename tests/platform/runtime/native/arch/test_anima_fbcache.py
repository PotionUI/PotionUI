"""FBCache integration on the Anima arch (seam in _dit_forward)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_anima_model import TINY, _build_ready

_T = torch.tensor([0.7])


def _text():
    return torch.randn(1, 5, 16), torch.randint(0, 100, (1, 7)), torch.ones(1, 7)


def _fwd(m, x, ctx, ids, w, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, t5xxl_ids=ids, t5xxl_weights=w, **kw)


def test_step_cache_none_is_byte_identical():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 16, 16)
    ctx, ids, w = _text()
    base = _fwd(m, x, ctx, ids, w)
    assert torch.equal(base, _fwd(m, x, ctx, ids, w, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 16, 16)
    ctx, ids, w = _text()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, ids, w, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, ids, w, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_different_inputs_do_not_skip():
    m = _build_ready(TINY)
    ctx, ids, w = _text()
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 8, 1, 16, 16), ctx, ids, w, step_cache=cache)
    _fwd(m, torch.randn(1, 8, 1, 16, 16) * 5.0, ctx, ids, w, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build_ready(TINY)  # num_blocks=2 -> blocks[-1] is block 1
    x = torch.randn(1, 8, 1, 16, 16)
    ctx, ids, w = _text()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    last = m.blocks[-1]
    orig = last.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    last.forward = counting
    _fwd(m, x, ctx, ids, w, step_cache=cache)
    assert calls["n"] == 1
    _fwd(m, x, ctx, ids, w, step_cache=cache)
    assert calls["n"] == 1


def test_resolution_change_forces_compute():
    m = _build_ready(TINY)
    ctx, ids, w = _text()
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 8, 1, 16, 16), ctx, ids, w, step_cache=cache)
    out = _fwd(m, torch.randn(1, 8, 1, 16, 32), ctx, ids, w, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape[-1] == 32
