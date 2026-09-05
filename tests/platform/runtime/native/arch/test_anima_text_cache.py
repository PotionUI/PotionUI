"""Run-scoped reuse of Anima's LLMAdapter text fusion (seam in ``_fuse_text``).

The fusion depends only on a guidance branch's text tensors, so a run must call
the adapter once per branch instead of once per branch per step, and must still
produce byte-identical velocities. Also pins the invalidation rules (T5 weights,
branch identity, weight revision) and that nothing survives the run.
"""

from __future__ import annotations

import types
import weakref

import torch

from src.platform.runtime.native.engine import NativeGenerator, NativeModel, RunCache

from .test_anima_model import TINY, _build_ready

_SIGMAS = [torch.tensor([0.9]), torch.tensor([0.6]), torch.tensor([0.3])]


def _branch(seed: int):
    g = torch.Generator().manual_seed(seed)
    return (
        torch.randn(1, 5, 16, generator=g),
        torch.randint(0, 100, (1, 7), generator=g),
        torch.rand(1, 7, generator=g),
    )


def _latents():
    g = torch.Generator().manual_seed(11)
    return [torch.randn(1, 8, 1, 16, 16, generator=g) for _ in _SIGMAS]


def _count_adapter(m) -> dict:
    orig = m.llm_adapter.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    m.llm_adapter.forward = counting
    return calls


def _run(m, branches, latents) -> list:
    outs = []
    with torch.no_grad():
        for sigma, x in zip(_SIGMAS, latents):
            for ctx, ids, w in branches:
                outs.append(m(x, sigma, ctx, t5xxl_ids=ids, t5xxl_weights=w))
    return outs


def test_cached_run_is_bytewise_identical_for_both_branches():
    m = _build_ready(TINY)
    branches = [_branch(1), _branch(2)]
    latents = _latents()

    uncached = _run(m, branches, latents)
    m.run_cache = RunCache(revision=1)
    cached = _run(m, branches, latents)

    assert len(cached) == 6
    for a, b in zip(uncached, cached):
        assert torch.equal(a, b)


def test_adapter_runs_once_per_branch_instead_of_once_per_step():
    m = _build_ready(TINY)
    branches = [_branch(1), _branch(2)]
    latents = _latents()
    calls = _count_adapter(m)

    _run(m, branches, latents)
    assert calls["n"] == 6  # 3 steps x 2 branches

    calls["n"] = 0
    m.run_cache = RunCache(revision=1)
    _run(m, branches, latents)
    assert calls["n"] == 2
    assert len(m.run_cache) == 2  # cond and uncond keep separate entries


def test_changed_t5_weights_recompute():
    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    m.run_cache = RunCache(revision=1)
    calls = _count_adapter(m)

    with torch.no_grad():
        first = m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        again = m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        reweighted = m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w * 2.0)

    assert calls["n"] == 2
    assert torch.equal(first, again)
    assert not torch.equal(first, reweighted)


def test_weight_revision_is_part_of_the_key():
    """A cache is dropped at run end, so a revision bump normally arrives with a
    fresh cache. Keying on it anyway is what makes an entry safe to keep across
    an in-place adapter change; this pins that the key really carries it."""
    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    dit = NativeModel("dit", m)
    cache = RunCache(dit.weight_revision)
    m.run_cache = cache
    calls = _count_adapter(m)

    with torch.no_grad():
        m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        m(x, _SIGMAS[1], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        assert calls["n"] == 1
        cache.revision = dit.bump_weight_revision("scheduled adapter window")
        m(x, _SIGMAS[2], ctx, t5xxl_ids=ids, t5xxl_weights=w)

    assert calls["n"] == 2


def test_third_branch_evicts_rather_than_growing():
    m = _build_ready(TINY)
    x = _latents()[0]
    m.run_cache = RunCache(revision=1)

    with torch.no_grad():
        for seed in (1, 2, 3):
            ctx, ids, w = _branch(seed)
            m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)

    assert len(m.run_cache) == 2


def test_engine_drops_the_run_cache_and_releases_its_tensors():
    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    gen = types.SimpleNamespace(dit=NativeModel("dit", m))

    with NativeGenerator._run_cache(gen):
        assert m.run_cache.revision == gen.dit.weight_revision
        with torch.no_grad():
            fused = m._fuse_text(x, ctx, ids, w)
        assert len(m.run_cache) == 1
        alive = weakref.ref(fused)
        del fused

    assert m.run_cache is None
    assert alive() is None


def test_model_eviction_releases_the_run_cache_tensors():
    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    dit = NativeModel("dit", m)
    cache = RunCache(dit.weight_revision)
    m.run_cache = cache

    with torch.no_grad():
        fused = m._fuse_text(x, ctx, ids, w)
    alive = weakref.ref(fused)
    del fused
    assert len(cache) == 1

    dit.unload()

    assert len(cache) == 0
    assert alive() is None
