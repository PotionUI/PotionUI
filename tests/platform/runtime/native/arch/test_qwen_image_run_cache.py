"""Run-cached step-invariant preparation on the Qwen-Image MMDiT forward.

Only ``x`` and ``timestep`` move between the steps of a denoise. The padding-mask
analysis (three host syncs), the packed clean reference tokens, the RoPE table
and the ``txt_norm``/``txt_in`` projection are properties of the guidance branch,
so ``QwenImageDiT`` prepares them once per branch into a ``RunCache`` entry.

These tests hold that reuse to two promises: the output is bit-identical to the
uncached path at every timestep, and a changed mask, reference, geometry or
weight revision is never answered from a stale entry. The call/sync counters
prove the work is actually being skipped rather than merely recomputed correctly.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager

import pytest
import torch

from src.platform.runtime.native.engine import RunCache

from .test_qwen_image_model import TINY, _build_ready

STEPS = (0.9, 0.5, 0.1)


def _inputs(batch=2, h=8, w=10, txt=6):
    x = torch.randn(batch, 4, 1, h, w)
    context = torch.randn(batch, txt, 12)
    return x, context


def _run(m, x, context, steps=STEPS, **kwargs):
    return [m(x, torch.full((x.shape[0],), t), context, **kwargs) for t in steps]


@contextmanager
def _cached(m, revision=1):
    """Attach a run cache the way ``NativeGenerator.sample`` does, then detach it.

    ``revision`` may be a one-element list, standing in for the live
    ``effective_revision`` a step-windowed LoRA moves mid-run.
    """
    holder = revision if isinstance(revision, list) else [revision]
    cache = RunCache(lambda: holder[0])
    m.run_cache = cache
    try:
        yield cache
    finally:
        cache.clear()
        m.run_cache = None


@contextmanager
def _count_syncs():
    """Count the device->host reads a forward performs (``.item()``, ``bool(t)``)."""
    counts = Counter()
    orig_item, orig_bool = torch.Tensor.item, torch.Tensor.__bool__

    def item(self):
        counts["item"] += 1
        return orig_item(self)

    def as_bool(self):
        counts["bool"] += 1
        return orig_bool(self)

    torch.Tensor.item, torch.Tensor.__bool__ = item, as_bool
    try:
        yield counts
    finally:
        torch.Tensor.item, torch.Tensor.__bool__ = orig_item, orig_bool


@contextmanager
def _count_prep(m):
    """Count the pieces of preparation the cache is meant to hoist out of a step."""
    counts = Counter()
    handles = [
        getattr(m, name).register_forward_pre_hook(
            lambda _mod, _inp, _name=name: counts.update([_name])
        )
        for name in ("pe_embedder", "txt_in", "txt_norm", "img_in")
    ]
    real_pack = m._pack_tokens

    def pack(x):
        counts["pack_tokens"] += 1
        return real_pack(x)

    m._pack_tokens = pack
    try:
        yield counts
    finally:
        del m._pack_tokens
        for handle in handles:
            handle.remove()


# --- equivalence ----------------------------------------------------------

CASES = {
    "plain": {},
    "right_padded_mask": {"attention_mask": torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])},
    "bool_mask": {"attention_mask": torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]]).bool()},
    "left_padded_mask": {"attention_mask": torch.tensor([[0, 0, 1, 1, 1, 1], [0, 1, 1, 1, 1, 1]])},
    "middle_padded_mask": {"attention_mask": torch.tensor([[1, 1, 0, 0, 1, 1], [1, 1, 1, 0, 0, 1]])},
    "all_real_mask": {"attention_mask": torch.ones(2, 6, dtype=torch.long)},
    "all_padding_row": {"attention_mask": torch.tensor([[1, 1, 1, 0, 0, 0], [0, 0, 0, 0, 0, 0]])},
    "float_mask": {"attention_mask": torch.zeros(2, 6)},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_cached_forward_is_bit_identical_to_uncached(name):
    """Every mask layout INF-Q01 distinguishes, run three timesteps with and
    without a run cache attached, must produce the same tensors bit for bit."""
    torch.manual_seed(0)
    m = _build_ready(TINY)
    x, context = _inputs()
    kwargs = CASES[name]

    uncached = _run(m, x, context, **kwargs)
    with _cached(m):
        cached = _run(m, x, context, **kwargs)

    for step, (a, b) in enumerate(zip(uncached, cached)):
        assert torch.equal(a, b), f"{name} diverged at step {step}"


@pytest.mark.parametrize("ref_method", ["index", "index_timestep_zero"])
def test_cached_forward_with_reference_latents_is_bit_identical(ref_method):
    """Clean references are packed once per run and concatenated onto the freshly
    packed noisy target each step; both ref methods must survive that."""
    torch.manual_seed(1)
    m = _build_ready(TINY)
    x, context = _inputs()
    refs = [torch.randn(2, 4, 1, 8, 10), torch.randn(2, 4, 1, 6, 6)]
    kwargs = {"ref_latents": refs, "ref_latents_method": ref_method,
              "attention_mask": torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])}

    uncached = _run(m, x, context, **kwargs)
    with _cached(m):
        cached = _run(m, x, context, **kwargs)

    assert all(torch.equal(a, b) for a, b in zip(uncached, cached))


def test_reference_tokens_are_joined_in_one_concatenation():
    """Two references contribute one prepared token block, not a per-step chain
    of concatenations, and it carries exactly their combined token count."""
    torch.manual_seed(2)
    m = _build_ready(TINY)
    x, context = _inputs()
    refs = [torch.randn(2, 4, 1, 8, 10), torch.randn(2, 4, 1, 6, 6)]

    prepared = m._prepare_fixed(x, context, None, refs, "index")
    expected = sum((r.shape[-2] // 2) * (r.shape[-1] // 2) for r in refs)
    assert prepared.ref_tokens.shape[1] == expected
    assert prepared.refs == tuple(refs)


# --- invalidation ---------------------------------------------------------

def test_changed_mask_is_not_answered_from_a_stale_entry():
    """A second branch with a different padding mask must be prepared afresh."""
    torch.manual_seed(3)
    m = _build_ready(TINY)
    x, context = _inputs()
    mask_a = torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])
    mask_b = torch.tensor([[1, 1, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0]])

    expected_b = _run(m, x, context, attention_mask=mask_b)
    with _cached(m):
        _run(m, x, context, attention_mask=mask_a)
        got_b = _run(m, x, context, attention_mask=mask_b)

    assert all(torch.equal(a, b) for a, b in zip(expected_b, got_b))


def test_changed_reference_latent_is_not_answered_from_a_stale_entry():
    """Swapping the reference tensor changes the prepared tokens; an entry keyed
    on the old one must not answer."""
    torch.manual_seed(4)
    m = _build_ready(TINY)
    x, context = _inputs()
    ref_a = [torch.randn(2, 4, 1, 8, 10)]
    ref_b = [torch.randn(2, 4, 1, 8, 10)]

    expected_b = _run(m, x, context, ref_latents=ref_b)
    with _cached(m):
        first = _run(m, x, context, ref_latents=ref_a)
        got_b = _run(m, x, context, ref_latents=ref_b)

    assert all(torch.equal(a, b) for a, b in zip(expected_b, got_b))
    assert not torch.equal(first[0], got_b[0])


def test_in_place_reference_write_is_not_answered_from_a_stale_entry():
    """``tensor_identity`` carries the version counter, so an in-place edit of a
    reference the cache already holds invalidates its entry."""
    torch.manual_seed(5)
    m = _build_ready(TINY)
    x, context = _inputs()
    ref = torch.randn(2, 4, 1, 8, 10)

    with _cached(m):
        before = _run(m, x, context, ref_latents=[ref], steps=(0.5,))
        ref.mul_(1.5)
        after = _run(m, x, context, ref_latents=[ref], steps=(0.5,))

    expected = _run(m, x, context, ref_latents=[ref], steps=(0.5,))
    assert torch.equal(after[0], expected[0])
    assert not torch.equal(before[0], after[0])


def test_revision_bump_invalidates_the_text_projection():
    """A step-windowed adapter changes ``txt_in`` mid-run and advances the live
    revision; the cached projection must not outlive it."""
    torch.manual_seed(6)
    m = _build_ready(TINY)
    x, context = _inputs()
    revision = [1]

    with _cached(m, revision):
        _run(m, x, context, steps=(0.5,))
        with torch.no_grad():
            m.txt_in.weight.mul_(1.3)
        revision[0] = 2
        after = _run(m, x, context, steps=(0.5,))

    expected = _run(m, x, context, steps=(0.5,))
    assert torch.equal(after[0], expected[0])


def test_revision_bump_without_the_key_would_still_drop_the_entry():
    """The key carries the revision AND ``RunCache`` clears on a moved revision.
    Either alone is enough; assert the entry is actually gone, not shadowed."""
    torch.manual_seed(7)
    m = _build_ready(TINY)
    x, context = _inputs()
    revision = [1]

    with _cached(m, revision) as cache:
        _run(m, x, context, steps=(0.5,))
        assert len(cache) == 1
        revision[0] = 9
        assert cache.get(("nothing",)) is None
        assert len(cache) == 0


def test_two_guidance_branches_share_one_bounded_cache():
    """Conditional and unconditional branches key separately and both stay
    correct; the run cache holds one entry each and grows no further."""
    torch.manual_seed(8)
    m = _build_ready(TINY)
    x, cond = _inputs()
    _, uncond = _inputs()

    expected_cond = _run(m, x, cond)
    expected_uncond = _run(m, x, uncond)

    with _cached(m) as cache:
        got_cond, got_uncond = [], []
        for t in STEPS:
            got_cond.append(m(x, torch.full((2,), t), cond))
            got_uncond.append(m(x, torch.full((2,), t), uncond))
        assert len(cache) == 2

    assert all(torch.equal(a, b) for a, b in zip(expected_cond, got_cond))
    assert all(torch.equal(a, b) for a, b in zip(expected_uncond, got_uncond))


def test_alternating_geometries_stay_correct_under_eviction():
    """Three distinct geometries exceed the cache's capacity of two. Correctness
    must not depend on a hit — an evicted entry is rebuilt, never approximated."""
    torch.manual_seed(9)
    m = _build_ready(TINY)
    context = torch.randn(2, 6, 12)
    shapes = [(8, 10), (12, 12), (6, 8)]
    xs = [torch.randn(2, 4, 1, h, w) for h, w in shapes]

    expected = [_run(m, x, context, steps=(0.5,))[0] for x in xs]
    with _cached(m):
        for _ in range(3):
            got = [_run(m, x, context, steps=(0.5,))[0] for x in xs]
            assert all(torch.equal(a, b) for a, b in zip(expected, got))


def test_unidentifiable_conditioning_is_never_cached():
    """A tensor whose identity cannot be established must fall through to the
    uncached path rather than key an entry."""
    torch.manual_seed(10)
    m = _build_ready(TINY)
    x, context = _inputs()

    class _NoVersion(torch.Tensor):
        @property
        def _version(self):
            raise RuntimeError("no version counter")

    opaque = context.as_subclass(_NoVersion)
    with _cached(m) as cache:
        got = _run(m, x, opaque, steps=(0.5,))
        assert len(cache) == 0

    expected = _run(m, x, context, steps=(0.5,))
    assert torch.equal(got[0], expected[0])


# --- work actually avoided ------------------------------------------------

def test_preparation_runs_once_per_run_not_once_per_step():
    """RoPE, the text projection and the reference packing are built on the first
    step only; the noisy target is still repacked and reprojected every step."""
    torch.manual_seed(11)
    m = _build_ready(TINY)
    x, context = _inputs()
    refs = [torch.randn(2, 4, 1, 8, 10), torch.randn(2, 4, 1, 6, 6)]
    kwargs = {"ref_latents": refs,
              "attention_mask": torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])}

    with _count_prep(m) as uncached:
        _run(m, x, context, **kwargs)
    with _cached(m):
        with _count_prep(m) as cached:
            _run(m, x, context, **kwargs)

    steps = len(STEPS)
    # three references packed per step uncached (target + two refs), one per step cached
    assert uncached["pack_tokens"] == 3 * steps
    assert cached["pack_tokens"] == steps + 2
    for piece in ("pe_embedder", "txt_in", "txt_norm"):
        assert uncached[piece] == steps, piece
        assert cached[piece] == 1, piece
    # the noisy target still goes through img_in on every step
    assert cached["img_in"] == uncached["img_in"] == steps


def test_mask_analysis_host_syncs_happen_once_per_run():
    """The padding analysis reads three scalars back from the device. Cached, a
    run pays that once instead of once per step."""
    torch.manual_seed(12)
    m = _build_ready(TINY)
    x, context = _inputs()
    mask = torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])

    with _count_syncs() as one_step:
        _run(m, x, context, steps=(0.5,), attention_mask=mask)
    with _count_syncs() as uncached:
        _run(m, x, context, attention_mask=mask)
    with _cached(m):
        with _count_syncs() as cached:
            _run(m, x, context, attention_mask=mask)

    steps = len(STEPS)
    per_step = one_step["item"] + one_step["bool"]
    assert per_step == 3
    assert uncached["item"] + uncached["bool"] == per_step * steps
    assert cached["item"] + cached["bool"] == per_step


def test_without_a_run_cache_every_step_prepares_its_own():
    """No cache attached is the pre-existing behaviour, unchanged: the arch must
    not quietly memoise on itself."""
    torch.manual_seed(13)
    m = _build_ready(TINY)
    x, context = _inputs()

    with _count_prep(m) as counts:
        _run(m, x, context)

    assert counts["pe_embedder"] == counts["txt_in"] == len(STEPS)
    assert getattr(m, "run_cache", None) is None


def test_entry_is_released_when_the_run_cache_is_cleared():
    """The engine drops the cache at run end; nothing may outlive it."""
    torch.manual_seed(14)
    m = _build_ready(TINY)
    x, context = _inputs()

    with _cached(m) as cache:
        _run(m, x, context, steps=(0.5,))
        assert len(cache) == 1
    assert len(cache) == 0
    assert m.run_cache is None
