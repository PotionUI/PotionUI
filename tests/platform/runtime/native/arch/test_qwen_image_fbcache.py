"""FBCache integration on the Qwen-Image arch: probe/skip seam + byte-identical default."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_qwen_image_model import TINY, _build_ready

_T = torch.tensor([0.5])


def _mask():
    return torch.ones(1, 5, dtype=torch.long)


def _fwd(m, x, ctx, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, attention_mask=_mask(), **kw)


def test_step_cache_none_is_byte_identical():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ctx = torch.randn(1, 5, 12)
    base = _fwd(m, x, ctx)
    assert torch.equal(base, _fwd(m, x, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_different_inputs_do_not_skip():
    m = _build_ready(TINY)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 4, 1, 8, 8), ctx, step_cache=cache)
    _fwd(m, torch.randn(1, 4, 1, 8, 8) * 5.0, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build_ready(TINY)  # num_layers=2 -> transformer_blocks[-1] is block 1
    x = torch.randn(1, 4, 1, 8, 8)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    last = m.transformer_blocks[-1]
    orig = last.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    last.forward = counting
    _fwd(m, x, ctx, step_cache=cache)   # compute: last block runs
    assert calls["n"] == 1
    _fwd(m, x, ctx, step_cache=cache)   # skip: last block must NOT run
    assert calls["n"] == 1


def test_control_payload_bypasses_step_cache():
    """S8: a ControlNet residual applied at a later block is invisible to the
    block-0 probe; a control payload must bypass the cache entirely — the
    passed-in FirstBlockCache is never consulted or updated (stats stay zero),
    rather than risk reusing a stale cached output under a changed residual."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.99, warmup_steps=0)
    control = {}  # presence alone triggers the bypass

    out1 = _fwd(m, x, ctx, step_cache=cache, control=control)
    out2 = _fwd(m, x, ctx, step_cache=cache, control=control)
    assert cache.stats() == {"computed": 0, "skipped": 0}
    assert torch.equal(out1, out2)


def test_resolution_change_forces_compute():
    m = _build_ready(TINY)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 4, 1, 8, 8), ctx, step_cache=cache)
    out = _fwd(m, torch.randn(1, 4, 1, 8, 12), ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape == (1, 4, 1, 8, 12)


def test_step_cache_works_with_index_timestep_zero_ref_latents():
    """index_timestep_zero doubles temb's batch dim only up to the
    block loop, collapsing it back before proj_out — the block-0 probe
    captured for FBCache must stay a plain (B, seq, dim) tensor, and skip/
    compute must behave exactly as without ref_latents."""
    m = _build_ready(TINY)
    x = torch.randn(1, 4, 1, 8, 8)
    ref = torch.randn(1, 4, 1, 8, 8)
    ctx = torch.randn(1, 5, 12)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    kw = dict(ref_latents=[ref], ref_latents_method="index_timestep_zero")
    first = _fwd(m, x, ctx, step_cache=cache, **kw)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache, **kw)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)
    assert first.shape == x.shape
