"""Run-scoped reuse of Anima's LLMAdapter text fusion (seam in ``_fuse_text``).

The fusion depends only on a guidance branch's text tensors, so a run must call
the adapter once per branch instead of once per branch per step, and must still
produce byte-identical velocities. Also pins the invalidation rules (T5 weights,
branch identity, weight revision) and that nothing survives the run.
"""

from __future__ import annotations

import types
import weakref

import pytest
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
    cache = RunCache(lambda: dit.effective_revision)
    m.run_cache = cache
    calls = _count_adapter(m)

    with torch.no_grad():
        m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        m(x, _SIGMAS[1], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        assert calls["n"] == 1
        dit.bump_weight_revision("scheduled adapter window")
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


# --- identity: in-place mutation, aliasing views, inference tensors ---------

def test_in_place_mutation_of_the_same_weights_tensor_recomputes():
    """The killer case for a (data_ptr, shape, dtype, device) key: every one of
    those components survives ``mul_``, so the stale fusion would be reused."""
    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    m.run_cache = RunCache(revision=1)
    calls = _count_adapter(m)

    with torch.no_grad():
        first = m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
        w.mul_(2.0)
        after = m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)

    assert calls["n"] == 2
    assert not torch.equal(first, after)


def test_different_stride_views_of_one_storage_are_separate_entries():
    """Two same-shape views over one storage share a ``data_ptr`` while
    addressing different elements; only stride/offset tell them apart."""
    base = torch.randn(1, 16, 16, generator=torch.Generator().manual_seed(5))
    a = base[:, :5, :]
    b = base[:, :, :5].transpose(1, 2)
    assert a.data_ptr() == b.data_ptr() and a.shape == b.shape
    assert not torch.equal(a, b)

    m = _build_ready(TINY)
    x = _latents()[0]
    _, ids, w = _branch(1)
    m.run_cache = RunCache(revision=1)
    calls = _count_adapter(m)

    with torch.no_grad():
        out_a = m(x, _SIGMAS[0], a, t5xxl_ids=ids, t5xxl_weights=w)
        out_b = m(x, _SIGMAS[0], b, t5xxl_ids=ids, t5xxl_weights=w)

    assert calls["n"] == 2
    assert not torch.equal(out_a, out_b)


def test_inference_mode_conditioning_is_cached_and_torch_forbids_mutating_it():
    """Text encoders encode under ``inference_mode``, so conditioning routinely
    arrives as inference tensors. They have no version counter, and are cached
    anyway because PyTorch refuses the in-place write the counter would catch —
    the second half of this test is that guarantee."""
    with torch.inference_mode():
        ctx = torch.randn(1, 5, 16)
        ids = torch.randint(0, 100, (1, 7))
        w = torch.rand(1, 7)
    assert ctx.is_inference() and ids.is_inference() and w.is_inference()

    m = _build_ready(TINY)
    latents = _latents()
    m.run_cache = RunCache(revision=1)
    calls = _count_adapter(m)

    with torch.no_grad():
        for sigma, x in zip(_SIGMAS, latents):
            m(x, sigma, ctx, t5xxl_ids=ids, t5xxl_weights=w)

    assert calls["n"] == 1
    with pytest.raises(RuntimeError, match="[Ii]nference"):
        w.mul_(2.0)


def test_unidentifiable_component_disables_caching():
    """A tensor whose identity cannot be established must not be keyed on."""
    from src.platform.runtime.native import cache_identity

    m = _build_ready(TINY)
    x = _latents()[0]
    ctx, ids, w = _branch(1)
    m.run_cache = RunCache(revision=1)
    calls = _count_adapter(m)

    real = cache_identity.tensor_identity

    def unidentifiable(t):
        return cache_identity.UNIDENTIFIABLE if t is w else real(t)

    import src.platform.runtime.native.arch.anima.model as anima_model

    anima_model.tensor_identity = unidentifiable
    try:
        with torch.no_grad():
            m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
            m(x, _SIGMAS[0], ctx, t5xxl_ids=ids, t5xxl_weights=w)
    finally:
        anima_model.tensor_identity = real

    assert calls["n"] == 2
    assert len(m.run_cache) == 0
