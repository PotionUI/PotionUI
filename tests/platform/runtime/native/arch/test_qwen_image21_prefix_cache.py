from __future__ import annotations

from contextlib import contextmanager

import torch

from src.platform.runtime.native.engine import RunCache
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_qwen_image21_model import TINY, _build_ready

STEPS = (0.9, 0.5, 0.1)

def _run(m, x, context, steps=STEPS, **kwargs):
    return [m(x, torch.full((x.shape[0],), t), context, **kwargs) for t in steps]

def _assert_close_seq(expected, got):
    for step, (a, b) in enumerate(zip(expected, got)):
        torch.testing.assert_close(a, b, atol=1e-5, rtol=1e-5, msg=lambda m, step=step: f"step {step}: {m}")

@contextmanager
def _cached(m, revision=1):
    holder = revision if isinstance(revision, list) else [revision]
    cache = RunCache(lambda: holder[0])
    m.run_cache = cache
    try:
        yield cache
    finally:
        cache.clear()
        m.run_cache = None


def test_cached_forward_txt2img_is_bit_identical_to_uncached():
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)

    uncached = _run(m, x, context)
    with _cached(m):
        cached = _run(m, x, context)

    _assert_close_seq(uncached, cached)

def test_cached_forward_with_refs_and_slots_is_bit_identical_to_uncached():
    torch.manual_seed(1)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ref_a = torch.randn(1, 8, 2, 2)
    ref_b = torch.randn(1, 8, 2, 2)
    context = torch.randn(1, 6, 12)
    kwargs = {"ref_latents": [ref_a, ref_b], "image_slots": [2, 4]}

    uncached = _run(m, x, context, **kwargs)
    with _cached(m):
        cached = _run(m, x, context, **kwargs)

    _assert_close_seq(uncached, cached)

def test_prefix_cache_hit_shrinks_block_input_to_target_rows_only():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    block0 = m.transformer_blocks[0]
    seen = []
    handle = block0.register_forward_pre_hook(lambda _m, inp: seen.append(inp[0].shape[1]))
    try:
        with _cached(m):
            _run(m, x, context, steps=(0.9, 0.5))
    finally:
        handle.remove()
    assert seen == [21, 16]


def test_cond_and_uncond_never_share_a_prefix_cache_entry():
    torch.manual_seed(2)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    cond = torch.randn(1, 5, 12)
    uncond = torch.randn(1, 5, 12)

    expected_cond = _run(m, x, cond)
    expected_uncond = _run(m, x, uncond)

    with _cached(m) as cache:
        got_cond, got_uncond = [], []
        for t in STEPS:
            got_cond.append(m(x, torch.full((1,), t), cond))
            got_uncond.append(m(x, torch.full((1,), t), uncond))
        assert len(cache) == 2

    _assert_close_seq(expected_cond, got_cond)
    _assert_close_seq(expected_uncond, got_uncond)


def test_changed_context_is_not_answered_from_a_stale_entry():
    torch.manual_seed(3)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    ctx_a = torch.randn(1, 5, 12)
    ctx_b = torch.randn(1, 5, 12)

    expected_b = _run(m, x, ctx_b)
    with _cached(m):
        _run(m, x, ctx_a)
        got_b = _run(m, x, ctx_b)

    _assert_close_seq(expected_b, got_b)

def test_changed_ref_latent_is_not_answered_from_a_stale_entry():
    torch.manual_seed(4)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 2, 2)
    context = torch.randn(1, 4, 12)
    ref_a = [torch.randn(1, 8, 1, 1)]
    ref_b = [torch.randn(1, 8, 1, 1)]

    expected_b = _run(m, x, context, ref_latents=ref_b)
    with _cached(m):
        first = _run(m, x, context, ref_latents=ref_a)
        got_b = _run(m, x, context, ref_latents=ref_b)

    _assert_close_seq(expected_b, got_b)
    assert not torch.equal(first[0], got_b[0])

def test_changed_target_shape_is_not_answered_from_a_stale_entry():
    torch.manual_seed(5)
    m = _build_ready(TINY)
    context = torch.randn(1, 5, 12)
    x_a = torch.randn(1, 8, 4, 4)
    x_b = torch.randn(1, 8, 4, 6)

    expected_b = _run(m, x_b, context)
    with _cached(m):
        _run(m, x_a, context)
        got_b = _run(m, x_b, context)

    _assert_close_seq(expected_b, got_b)

def test_new_run_gets_a_fresh_cache():
    torch.manual_seed(6)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)

    with _cached(m) as first_run:
        run1 = _run(m, x, context, steps=(0.5,))
        assert len(first_run) == 1

    with _cached(m) as second_run:
        run2 = _run(m, x, context, steps=(0.5,))
        assert len(second_run) == 1

    assert torch.equal(run1[0], run2[0])

def test_revision_bump_invalidates_the_prefix_cache():
    torch.manual_seed(7)
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    revision = [1]

    with _cached(m, revision):
        _run(m, x, context, steps=(0.5,))
        with torch.no_grad():
            m.txt_in.in_layer.weight.mul_(1.3)
        revision[0] = 2
        after = _run(m, x, context, steps=(0.5,))

    expected = _run(m, x, context, steps=(0.5,))
    assert torch.equal(after[0], expected[0])


def test_step_cache_and_prefix_cache_compose():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)

    with _cached(m):
        step_cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
        first = m(x, torch.tensor([0.5]), context, step_cache=step_cache)
        assert step_cache.stats() == {"computed": 1, "skipped": 0}
        second = m(x, torch.tensor([0.5]), context, step_cache=step_cache)
        assert step_cache.stats() == {"computed": 1, "skipped": 1}

    assert torch.equal(second, first)
    uncached = m(x, torch.tensor([0.5]), context)
    torch.testing.assert_close(first, uncached, atol=1e-5, rtol=1e-5)

def test_without_a_run_cache_prefix_kv_is_never_cached():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)
    _run(m, x, context, steps=(0.5,))
    assert getattr(m, "run_cache", None) is None

def test_entry_is_released_when_the_run_cache_is_cleared():
    m = _build_ready(TINY)
    x = torch.randn(1, 8, 4, 4)
    context = torch.randn(1, 5, 12)

    with _cached(m) as cache:
        _run(m, x, context, steps=(0.5,))
        assert len(cache) == 1
    assert len(cache) == 0
    assert m.run_cache is None
