"""FBCache integration on the Qwen-Image-2.1 single-stream arch: probe/skip
seam, target-rows-only probe, and byte-identical default."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_qwen_image21_model import TINY, _build_ready

_T = torch.tensor([0.5])


def _ctx():
    return torch.randn(1, 5, 12)


def _fwd(m, x, ctx, **kw):
    with torch.no_grad():
        return m(x, _T, ctx, **kw)


def test_step_cache_none_is_byte_identical():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ctx = _ctx()
    base = _fwd(m, x, ctx)
    assert torch.equal(base, _fwd(m, x, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_different_inputs_do_not_skip():
    m = _build_ready(TINY)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, torch.randn(1, 8, 4, 4), ctx, step_cache=cache)
    _fwd(m, torch.randn(1, 8, 4, 4) * 5.0, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build_ready(TINY)  # num_layers=2 -> transformer_blocks[-1] is block 1
    x = torch.randn(1, 8, 4, 4)
    ctx = _ctx()
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


def test_resolution_change_forces_compute():
    m = _build_ready(TINY)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, torch.randn(1, 8, 4, 4), ctx, step_cache=cache)
    out = _fwd(m, torch.randn(1, 8, 4, 8), ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape == (1, 8, 4, 8)


def test_skip_preserves_input_latent_rank():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 1, 4, 4)  # 5-D latent (extra singleton axis)
    ctx = _ctx()
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, x, ctx, step_cache=cache)
    second = _fwd(m, x, ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 1
    assert second.shape == x.shape == first.shape


def test_probe_is_target_rows_only_not_prefix():
    """The probe fed to the cache must be block-0's TARGET rows
    (hidden_states[:, prefix_len:]) only -- text ("prefix") rows are pinned
    to t=0 every step and would dilute the drift signal if included."""
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)  # target: 4*4 = 16 tokens
    ctx = _ctx()  # text: 5 tokens
    cache = FirstBlockCache(rel_threshold=0.0, warmup_steps=0)  # never skips; just records
    _fwd(m, x, ctx, step_cache=cache)
    assert cache.prev_probe.shape == (1, 16, m.config.inner_dim)


def test_with_ref_latents_probe_excludes_prefix_and_reference_rows():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)  # target: 16 tokens
    ref = torch.randn(1, 8, 4, 4)  # reference block: 16 tokens (prefix)
    ctx = _ctx()  # text: 5 tokens (prefix)
    cache = FirstBlockCache(rel_threshold=0.0, warmup_steps=0)
    _fwd(m, x, ctx, step_cache=cache, ref_latents=[ref])
    assert cache.prev_probe.shape == (1, 16, m.config.inner_dim)
