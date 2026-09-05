"""Run-scoped reuse of Flux's text preparation and sampling geometry.

Between the steps of a denoise only the noisy latent and the timestep move. The
text projection (``txt_norm``/``txt_in``), the pooled-CLIP and distilled-guidance
contributions to ``vec``, the RoPE table over the joint ``[txt | img | ref]`` id
sequence, the joint key-padding mask and the packed clean reference tokens are
all properties of the guidance branch, so ``Flux`` prepares them once per branch
into a ``RunCache`` entry (``_branch_inputs`` -> ``_FluxPrepared``).

The seam is exercised through the real ``forward`` and, for lifetime, through the
engine's real run scope (``NativeGenerator._run_cache``). Coverage:

  (a) a cached run is bit-identical to an uncached one at every timestep, for
      both variants, every reference method, masks and circular padding;
  (b) preparation happens once per run instead of once per step, and two guidance
      branches coexist in the two-entry cache without evicting each other;
  (c) a changed mask, prompt, latent geometry, reference or weight revision is
      never answered from an entry keyed on the previous one;
  (d) an unidentifiable conditioning tensor bypasses the cache entirely;
  (e) the run scope releases the cache, including on cancellation, and the model
      retains nothing of its own.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.engine import NativeGenerator, NativeModel, RunCache

from .test_flux_model import TINY_FLUX1, TINY_FLUX2, _build_ready

STEPS = (0.9, 0.5, 0.1)


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
def _count_prep(m):
    """Count the preparation the cache is meant to hoist out of a step."""
    counts = Counter()
    handles = []
    for name in ("pe_embedder", "txt_in", "img_in", "time_in", "vector_in", "guidance_in"):
        sub = getattr(m, name, None)
        if isinstance(sub, torch.nn.Module):
            handles.append(sub.register_forward_pre_hook(
                lambda _mod, _inp, _name=name: counts.update([_name])))
    real_prepare, real_pack = m._prepare_branch, m._pack_tokens

    def prepare(*a, **kw):
        counts["prepare_branch"] += 1
        return real_prepare(*a, **kw)

    def pack(x):
        counts["pack_tokens"] += 1
        return real_pack(x)

    m._prepare_branch, m._pack_tokens = prepare, pack
    try:
        yield counts
    finally:
        del m._prepare_branch, m._pack_tokens
        for handle in handles:
            handle.remove()


def _run(m, xs, context, **kwargs):
    with torch.no_grad():
        return [m(x, torch.full((x.shape[0],), t), context, **kwargs)
                for x, t in zip(xs, STEPS)]


def _latents(batch=2, ch=16, h=8, w=10, seed=1):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(batch, ch, h, w, generator=g)
    return [x * s for s in STEPS]


# --- (a) equivalence ------------------------------------------------------

MASK = torch.tensor([[1, 1, 1, 1, 0, 0], [1, 1, 0, 0, 0, 0]])


def _flux2_case(name):
    g = torch.Generator().manual_seed(4)
    context = torch.randn(2, 6, 32, generator=g)
    refs = [torch.randn(2, 16, 8, 10, generator=g), torch.randn(2, 16, 4, 6, generator=g)]
    xs = _latents()
    kwargs = {"context": context}
    if name == "masked":
        kwargs["attention_mask"] = MASK
    elif name == "all_real_mask":
        kwargs["attention_mask"] = torch.ones(2, 6, dtype=torch.long)
    elif name.startswith("refs_"):
        kwargs.update(ref_latents=refs, ref_latents_method=name[len("refs_"):],
                      attention_mask=MASK)
    elif name == "odd_hw":
        xs = [x[:, :, :7, :9] for x in xs]
    return xs, kwargs


FLUX2_CASES = ["plain", "masked", "all_real_mask", "refs_index", "refs_uxo",
               "refs_offset", "odd_hw"]


@pytest.mark.parametrize("name", FLUX2_CASES)
def test_a_cached_flux2_run_is_bit_identical(name):
    m = _build_ready(TINY_FLUX2)
    xs, kwargs = _flux2_case(name)

    uncached = _run(m, xs, **kwargs)
    with _cached(m):
        cached = _run(m, xs, **kwargs)

    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)
    # the noisy latent and the timestep are live inputs, not cached ones
    assert not torch.equal(cached[0], cached[1])


def _flux1_case(name):
    g = torch.Generator().manual_seed(5)
    context = torch.randn(2, 5, 32, generator=g)
    kwargs = {"context": context, "y": torch.randn(2, 768, generator=g),
              "guidance": torch.full((2,), 3.5)}
    xs = _latents(h=8, w=8, seed=6)
    if name == "no_pooled_vector":
        del kwargs["y"]
    elif name == "no_guidance":
        del kwargs["guidance"]
    elif name == "refs":
        kwargs["ref_latents"] = [torch.randn(2, 16, 8, 8, generator=g)]
    elif name == "odd_hw":
        xs = [x[:, :, :7, :7] for x in xs]
    return xs, kwargs


@pytest.mark.parametrize(
    "name", ["pooled_and_guidance", "no_pooled_vector", "no_guidance", "refs", "odd_hw"])
def test_a_cached_flux1_run_is_bit_identical(name):
    """Flux1 adds the two ``vec`` terms the cache holds: the pooled-CLIP
    projection and the distilled guidance embedding."""
    m = _build_ready(TINY_FLUX1)
    xs, kwargs = _flux1_case(name)

    uncached = _run(m, xs, **kwargs)
    with _cached(m):
        cached = _run(m, xs, **kwargs)

    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)
    assert not torch.equal(cached[0], cached[1])


def test_a_controlnet_run_keeps_its_residuals_live():
    """ControlNet residuals reach the blocks per step and bypass the step cache;
    neither may be folded into the branch entry."""
    m = _build_ready(TINY_FLUX2)
    g = torch.Generator().manual_seed(7)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=g)
    control = {"input": [torch.randn(2, 80, 64, generator=g) * 0.01],
               "output": [torch.randn(2, 80, 64, generator=g) * 0.01]}

    uncached = _run(m, xs, context, control=control)
    with _cached(m):
        cached = _run(m, xs, context, control=control)
    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)

    with _cached(m):
        without = _run(m, xs, context)
    assert not torch.equal(cached[0], without[0])


# --- (b) work actually avoided -------------------------------------------

def test_preparation_runs_once_per_run_not_once_per_step():
    m = _build_ready(TINY_FLUX2)
    g = torch.Generator().manual_seed(8)
    xs = _latents()
    kwargs = {"context": torch.randn(2, 6, 32, generator=g),
              "attention_mask": MASK,
              "ref_latents": [torch.randn(2, 16, 8, 10, generator=g)]}

    with _count_prep(m) as uncached:
        _run(m, xs, **kwargs)
    with _cached(m):
        with _count_prep(m) as cached:
            _run(m, xs, **kwargs)

    steps = len(STEPS)
    # every step repacks + reprojects the noisy target and rebuilds the timestep;
    # the one reference is packed per step uncached and once per run cached
    assert uncached["pack_tokens"] == 2 * steps
    assert cached["pack_tokens"] == steps + 1
    assert uncached["img_in"] == cached["img_in"] == steps
    assert uncached["time_in"] == cached["time_in"] == steps
    # the branch preparation collapses to one
    for piece in ("prepare_branch", "pe_embedder", "txt_in"):
        assert uncached[piece] == steps, piece
        assert cached[piece] == 1, piece


def test_the_pooled_and_guidance_terms_are_embedded_once_per_run():
    m = _build_ready(TINY_FLUX1)
    g = torch.Generator().manual_seed(9)
    kwargs = {"context": torch.randn(2, 5, 32, generator=g),
              "y": torch.randn(2, 768, generator=g), "guidance": torch.full((2,), 3.5)}
    xs = _latents(h=8, w=8)

    with _count_prep(m) as uncached:
        _run(m, xs, **kwargs)
    with _cached(m):
        with _count_prep(m) as cached:
            _run(m, xs, **kwargs)

    assert (uncached["vector_in"], uncached["guidance_in"]) == (len(STEPS), len(STEPS))
    assert (cached["vector_in"], cached["guidance_in"]) == (1, 1)


def test_two_guidance_branches_coexist_without_evicting_each_other():
    """A CFG run alternates cond/uncond forwards; neither may thrash the other."""
    m = _build_ready(TINY_FLUX2)
    g = torch.Generator().manual_seed(10)
    xs = _latents()
    cond = torch.randn(2, 6, 32, generator=g)
    uncond = torch.randn(2, 6, 32, generator=g)

    want = [(a, b) for a, b in zip(_run(m, xs, cond), _run(m, xs, uncond))]

    with _cached(m) as cache:
        with _count_prep(m) as counts:
            with torch.no_grad():
                got = [(m(x, torch.full((2,), t), cond), m(x, torch.full((2,), t), uncond))
                       for x, t in zip(xs, STEPS)]
        assert len(cache) == 2

    assert counts["prepare_branch"] == 2
    for (got_c, got_u), (want_c, want_u) in zip(got, want):
        assert torch.equal(got_c, want_c)
        assert torch.equal(got_u, want_u)


# --- (c) key components ---------------------------------------------------

def test_a_changed_text_mask_is_never_answered_from_the_cache():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(11))
    padded = MASK
    full = torch.ones(2, 6, dtype=torch.long)

    want_padded = _run(m, xs, context, attention_mask=padded)[0]
    want_full = _run(m, xs, context, attention_mask=full)[0]
    assert not torch.equal(want_padded, want_full)

    with _cached(m), _count_prep(m) as counts, torch.no_grad():
        t = torch.full((2,), STEPS[0])
        assert torch.equal(m(xs[0], t, context, attention_mask=padded), want_padded)
        assert torch.equal(m(xs[0], t, context, attention_mask=full), want_full)
    assert counts["prepare_branch"] == 2


def test_a_changed_prompt_is_never_answered_from_the_cache():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    g = torch.Generator().manual_seed(12)
    a, b = torch.randn(2, 6, 32, generator=g), torch.randn(2, 6, 32, generator=g)

    want_a, want_b = _run(m, xs, a)[0], _run(m, xs, b)[0]
    assert not torch.equal(want_a, want_b)

    with _cached(m), torch.no_grad():
        t = torch.full((2,), STEPS[0])
        assert torch.equal(m(xs[0], t, a), want_a)
        assert torch.equal(m(xs[0], t, b), want_b)


def test_a_changed_pooled_vector_or_guidance_is_never_answered_from_the_cache():
    """Flux1's two ``vec`` terms are branch state: a CFG run whose branches differ
    only in the pooled CLIP vector, or a guidance sweep, must not cross-answer."""
    m = _build_ready(TINY_FLUX1)
    xs = _latents(h=8, w=8, seed=20)
    g = torch.Generator().manual_seed(21)
    context = torch.randn(2, 5, 32, generator=g)
    y_a, y_b = torch.randn(2, 768, generator=g), torch.randn(2, 768, generator=g)
    guid_a, guid_b = torch.full((2,), 3.5), torch.full((2,), 7.0)

    with torch.no_grad():
        t = torch.full((2,), STEPS[0])
        want_ya = m(xs[0], t, context, y=y_a, guidance=guid_a)
        want_yb = m(xs[0], t, context, y=y_b, guidance=guid_a)
        want_gb = m(xs[0], t, context, y=y_a, guidance=guid_b)
        assert not torch.equal(want_ya, want_yb)
        assert not torch.equal(want_ya, want_gb)

        with _cached(m):
            assert torch.equal(m(xs[0], t, context, y=y_a, guidance=guid_a), want_ya)
            assert torch.equal(m(xs[0], t, context, y=y_b, guidance=guid_a), want_yb)
        with _cached(m):
            assert torch.equal(m(xs[0], t, context, y=y_a, guidance=guid_a), want_ya)
            assert torch.equal(m(xs[0], t, context, y=y_a, guidance=guid_b), want_gb)


def test_a_changed_latent_geometry_is_never_answered_from_the_cache():
    """Padding included: 7x9 circular-pads to 8x10 but keeps its own id grid."""
    m = _build_ready(TINY_FLUX2)
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(13))
    shapes = [(8, 10), (12, 12), (7, 9)]
    xs = [torch.randn(2, 16, h, w, generator=torch.Generator().manual_seed(h * w))
          for h, w in shapes]

    with torch.no_grad():
        t = torch.full((2,), 0.5)
        want = [m(x, t, context) for x in xs]
        with _cached(m):
            for _ in range(3):
                assert all(torch.equal(m(x, t, context), w) for x, w in zip(xs, want))


def test_a_changed_reference_is_never_answered_from_the_cache():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    g = torch.Generator().manual_seed(14)
    context = torch.randn(2, 6, 32, generator=g)
    big = [torch.randn(2, 16, 8, 10, generator=g)]
    small = [torch.randn(2, 16, 4, 6, generator=g)]

    want_big = _run(m, xs, context, ref_latents=big)[0]
    want_small = _run(m, xs, context, ref_latents=small)[0]
    assert not torch.equal(want_big, want_small)

    with _cached(m), torch.no_grad():
        t = torch.full((2,), STEPS[0])
        assert torch.equal(m(xs[0], t, context, ref_latents=big), want_big)
        assert torch.equal(m(xs[0], t, context, ref_latents=small), want_small)


def test_a_bumped_revision_invalidates_the_prepared_branch():
    """The text projection is computed with the weights in effect at the time, so
    a mid-run weight change (a step-windowed LoRA edge) must not be answered from
    an entry keyed on the previous revision."""
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(15))
    revision = [1]

    with _cached(m, revision) as cache, _count_prep(m) as counts, torch.no_grad():
        t = torch.full((2,), STEPS[0])
        before = m(xs[0], t, context)
        assert torch.equal(m(xs[0], t, context), before)
        assert counts["prepare_branch"] == 1

        with torch.no_grad():
            m.txt_in.weight.add_(0.05)
        revision[0] = 2
        after = m(xs[0], t, context)

        assert counts["prepare_branch"] == 2, "a stale projection answered after the edge"
        assert not torch.equal(after, before)
        assert len(cache) == 1, "the superseded entry is dropped, not merely bypassed"


# --- (d) unidentifiable input --------------------------------------------

def test_unidentifiable_conditioning_is_never_cached():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(16))

    class _NoVersion(torch.Tensor):
        @property
        def _version(self):
            raise RuntimeError("no version counter")

    opaque = context.as_subclass(_NoVersion)
    with _cached(m) as cache, _count_prep(m) as counts:
        got = _run(m, xs, opaque)
        assert len(cache) == 0
        assert counts["prepare_branch"] == len(STEPS)

    want = _run(m, xs, context)
    assert torch.equal(got[0], want[0])


# --- (e) lifetime ---------------------------------------------------------

def _run_scope(module):
    """The engine's own run scope, attached to a real ``NativeModel`` wrapper."""
    dit = NativeModel("diffusion_model", module)
    return dit, NativeGenerator._run_cache(SimpleNamespace(dit=dit))


def test_the_engine_run_scope_releases_the_cache_on_cancellation():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(17))
    _, scope = _run_scope(m)

    with pytest.raises(RuntimeError, match="cancelled"):
        with scope:
            _run(m, xs[:1], context)
            cache = m.run_cache
            assert len(cache) == 1
            raise RuntimeError("cancelled")

    assert m.run_cache is None
    assert len(cache) == 0


def test_the_model_retains_nothing_of_its_own_between_runs():
    """Every reused tensor hangs off the engine's run cache, never off the model."""
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    context = torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(18))

    _, scope = _run_scope(m)
    with scope:
        _run(m, xs, context)
    assert [k for k, v in vars(m).items() if isinstance(v, torch.Tensor)] == []

    with _count_prep(m) as counts:
        _run(m, xs, context)
    assert counts["prepare_branch"] == len(STEPS)


def test_without_a_run_cache_the_forward_is_unchanged():
    m = _build_ready(TINY_FLUX2)
    xs = _latents()
    assert getattr(m, "run_cache", None) is None
    out = _run(m, xs, torch.randn(2, 6, 32, generator=torch.Generator().manual_seed(19)))[0]
    assert out.shape == xs[0].shape
    assert torch.isfinite(out).all()
