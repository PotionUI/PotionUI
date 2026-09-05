"""Z-Image reuses the refined caption and the position frequencies across a run.

The caption stream (``cap_embedder`` -> learned pad -> the whole
``context_refiner`` stack) is called with ``adaln_input=None`` on blocks built
with ``modulation=False``, so it depends on the guidance branch and the weights
alone. The position ids and their RoPE depend on the sequence shape alone. Both
were rebuilt on every one of a run's forwards.

The reuse lives entirely in the engine's per-run ``RunCache``: without one
attached every forward computes from scratch, so the model retains nothing of its
own between runs and eviction is the engine's existing teardown.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.z_image.model import ZImageDiT
from src.platform.runtime.native.cache_identity import tensor_identity
from src.platform.runtime.native.engine import RunCache

from .test_z_image_model import ZTINY, _build

STEPS = (0.9, 0.5, 0.1)


def _context(cap_len: int, bsz: int = 1, dtype=torch.float32) -> torch.Tensor:
    return torch.randn(bsz, cap_len, ZTINY["cap_feat_dim"], dtype=dtype)


def _latent(bsz: int, h: int, w: int, dtype=torch.float32) -> torch.Tensor:
    return torch.randn(bsz, 16, h, w, dtype=dtype)


def _run(m: ZImageDiT, x, contexts, bsz: int = 1) -> list[torch.Tensor]:
    """One denoise-shaped run: every timestep, every guidance branch, in order."""
    out = []
    with torch.no_grad():
        for sigma in STEPS:
            t = torch.full((bsz,), sigma, dtype=torch.float32)
            for ctx in contexts:
                out.append(m(x, t, ctx))
    return out


class _Counts:
    """Call counts for the three paths a run should stop repeating."""

    def __init__(self, m: ZImageDiT) -> None:
        self.cap_embedder = 0
        self.context_refiner = 0
        self.rope = 0
        self._patch(m.cap_embedder, "cap_embedder")
        self._patch(m.context_refiner[0], "context_refiner")
        self._patch(m.rope_embedder, "rope")

    def _patch(self, module, field: str) -> None:
        orig = module.forward

        def spy(*args, **kwargs):
            setattr(self, field, getattr(self, field) + 1)
            return orig(*args, **kwargs)

        module.forward = spy

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.cap_embedder, self.context_refiner, self.rope)


class _Revision:
    """A live weight revision, the shape ``RunCache`` reads from the engine.

    The engine hands ``RunCache`` a zero-argument accessor rather than a number so
    a step-windowed adapter applying mid-run is seen at the next lookup. ``bump``
    stands in for that adapter reaching a window boundary.
    """

    def __init__(self, value: int = 1) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def bump(self) -> int:
        self.value += 1
        return self.value


def _attach(m: ZImageDiT) -> tuple[RunCache, _Revision]:
    live = _Revision()
    cache = RunCache(live)
    m.run_cache = cache
    return cache, live


def _geometry_key(m: ZImageDiT, like: torch.Tensor, bsz: int, h: int, w: int,
                  ctx_len: int, revision: int = 1):
    """The run-cache key one geometry occupies — spelled out, not read back.

    Device and dtype come from a tensor the test actually passed in, never from a
    literal: a suite that ran earlier and set a torch default would otherwise make
    this helper disagree with the model for reasons unrelated to the cache.
    """
    p = m.patch_size
    cap_len = ctx_len + (-ctx_len) % m.pad_tokens_multiple
    return ("z_image.geometry", revision, bsz, -(-h // p), -(-w // p), cap_len,
            like.device, like.dtype)


# -- equivalence ------------------------------------------------------------


def _assert_cached_matches_uncached(bsz, cap_lens, h, w, dtype=torch.float32, tol=0.0):
    m = _build(dtype)
    x = _latent(bsz, h, w, dtype)
    contexts = [_context(n, bsz, dtype) for n in cap_lens]

    m.run_cache = None
    cold = _run(m, x, contexts, bsz)
    _attach(m)
    warm = _run(m, x, contexts, bsz)

    assert len(warm) == len(cold) == len(STEPS) * len(contexts)
    for i, (a, b) in enumerate(zip(cold, warm)):
        assert torch.allclose(a, b, atol=tol, rtol=0), (i, bsz, cap_lens, h, w)


def test_cached_run_matches_uncached_short_prompt_both_branches():
    # 3 caption tokens: the learned cap_pad_token pads 3 -> 32.
    _assert_cached_matches_uncached(bsz=1, cap_lens=(3, 5), h=16, w=16)


def test_cached_run_matches_uncached_long_prompt_both_branches():
    # 40 caption tokens crosses the pad multiple: pads 40 -> 64.
    _assert_cached_matches_uncached(bsz=2, cap_lens=(40, 33), h=16, w=16)


def test_cached_run_matches_uncached_on_odd_dimensions():
    # 7x11 circular-pads to 8x12 -> 24 image tokens, x-padded up to 32: the
    # branch where the cached image position ids had to be extended.
    _assert_cached_matches_uncached(bsz=2, cap_lens=(3, 40), h=7, w=11)


def test_cached_run_matches_uncached_bf16():
    _assert_cached_matches_uncached(bsz=1, cap_lens=(3, 5), h=16, w=16,
                                    dtype=torch.bfloat16, tol=0.0)


# -- op counts --------------------------------------------------------------


def test_caption_and_geometry_are_built_once_per_run():
    m = _build()
    x = _latent(1, 16, 16)
    contexts = [_context(3), _context(3)]
    _attach(m)
    counts = _Counts(m)

    _run(m, x, contexts)

    # Two branches: one caption stream each, and one geometry for the run
    # (rope_embedder runs twice per build — caption ids, then image ids).
    assert counts.as_tuple() == (2, 2, 2)


def test_without_a_run_cache_every_forward_rebuilds_everything():
    m = _build()
    x = _latent(1, 16, 16)
    contexts = [_context(3), _context(3)]
    m.run_cache = None
    counts = _Counts(m)

    _run(m, x, contexts)

    assert counts.as_tuple() == (6, 6, 12)


def test_the_joint_stack_still_runs_every_step():
    """Only the pre-joint caption work is hoisted — nothing after it."""
    m = _build()
    x = _latent(1, 16, 16)
    contexts = [_context(3), _context(3)]
    _attach(m)
    noise_refiner = 0
    joint = 0

    orig_noise = m.noise_refiner[0].forward
    orig_joint = m.layers[0].forward

    def spy_noise(*a, **k):
        nonlocal noise_refiner
        noise_refiner += 1
        return orig_noise(*a, **k)

    def spy_joint(*a, **k):
        nonlocal joint
        joint += 1
        return orig_joint(*a, **k)

    m.noise_refiner[0].forward = spy_noise
    m.layers[0].forward = spy_joint

    _run(m, x, contexts)

    assert noise_refiner == 6
    assert joint == 6


# -- keys -------------------------------------------------------------------


def test_each_branch_gets_its_own_entry_and_keeps_it():
    m = _build()
    x = _latent(1, 16, 16)
    cond, uncond = _context(3), _context(3)
    cache, _ = _attach(m)
    counts = _Counts(m)

    _run(m, x, [cond, uncond])

    geometry = cache.get(_geometry_key(m, x, 1, 16, 16, 3))
    assert geometry is not None
    assert len(geometry.captions) == 2
    # Returning to the first branch after the second must still be a hit.
    assert counts.cap_embedder == 2


def test_a_third_caption_evicts_rather_than_growing():
    m = _build()
    x = _latent(1, 16, 16)
    cache, _ = _attach(m)

    _run(m, x, [_context(3), _context(3), _context(3)])

    geometry = cache.get(_geometry_key(m, x, 1, 16, 16, 3))
    assert len(geometry.captions) == 2
    assert len(cache) == 1


def test_a_bumped_weight_revision_recomputes_the_caption():
    """The revision is read fresh at every lookup, not captured at run start.

    A step-windowed adapter applies and restores DURING a run, bumping the DiT's
    effective revision mid-flight, so a caption refined under the old weights must
    not answer a lookup made after the bump.
    """
    m = _build()
    x = _latent(1, 16, 16)
    ctx = _context(3)
    cache, live = _attach(m)
    counts = _Counts(m)

    _run(m, x, [ctx])
    assert counts.cap_embedder == 1

    live.bump()
    _run(m, x, [ctx])
    assert counts.cap_embedder == 2


def test_both_branches_recompute_once_after_a_bump_then_hit_again():
    """A window bump costs one recompute per branch, not one per step."""
    m = _build()
    x = _latent(1, 16, 16)
    cond, uncond = _context(3), _context(3)
    cache, live = _attach(m)
    counts = _Counts(m)

    _run(m, x, [cond, uncond])
    assert counts.cap_embedder == 2

    live.bump()
    _run(m, x, [cond, uncond])
    assert counts.cap_embedder == 4

    geometry = cache.get(_geometry_key(m, x, 1, 16, 16, 3, revision=live.value))
    assert len(geometry.captions) == 2  # the stale pair replaced, not accumulated


def test_the_caption_key_leads_with_the_weight_revision():
    """Asserted on the key, because behaviour alone cannot show it.

    ``RunCache`` drops every entry when the revision moves, so a caption key that
    omitted the revision would still never serve a stale hit through the engine
    and no behavioural test could tell the two apart. Carrying it is the cache's
    stated contract and the only line of defence if that backstop is ever
    narrowed, so pin the key itself.
    """
    m = _build()
    x = _latent(1, 16, 16)
    cache, live = _attach(m)

    _run(m, x, [_context(3), _context(3)])

    geometry = cache.get(_geometry_key(m, x, 1, 16, 16, 3))
    assert [k[0] for k in geometry.captions] == [live.value, live.value]


def test_a_bump_leaves_nothing_keyed_under_the_old_revision():
    """The run cache drops superseded entries; nothing of the old run is retained."""
    m = _build()
    x = _latent(1, 16, 16)
    ctx = _context(3)
    cache, live = _attach(m)

    _run(m, x, [ctx])
    stale = _geometry_key(m, x, 1, 16, 16, 3, revision=1)
    assert cache.get(stale) is not None

    live.bump()
    _run(m, x, [ctx])

    assert cache.get(stale) is None
    assert len(cache) == 1


def test_a_new_geometry_is_built_when_the_token_grid_changes():
    m = _build()
    ctx = _context(3)
    _attach(m)
    counts = _Counts(m)

    _run(m, _latent(1, 16, 16), [ctx])
    _run(m, _latent(1, 8, 8), [ctx])

    assert counts.rope == 4


# -- caption identity -------------------------------------------------------


def test_an_in_place_write_to_the_caption_is_not_a_hit():
    """``context.mul_(2)`` leaves address, shape, dtype and device untouched.

    Nothing in the sampling path writes to conditioning mid-run, but the key must
    not be the thing standing between that and a silently wrong image.
    """
    m = _build()
    x = _latent(1, 16, 16)
    ctx = _context(3)
    _attach(m)
    counts = _Counts(m)

    with torch.no_grad():
        before = m(x, torch.full((1,), 0.5), ctx)
        ctx.mul_(2.0)
        after = m(x, torch.full((1,), 0.5), ctx)

    assert counts.cap_embedder == 2
    assert not torch.allclose(before, after)


def test_two_strided_views_of_one_storage_are_not_the_same_caption():
    """Same address, same shape, different stride, different elements."""
    m = _build()
    x = _latent(1, 16, 16)
    square = torch.randn(1, ZTINY["cap_feat_dim"], ZTINY["cap_feat_dim"])
    transposed = square.transpose(1, 2)
    assert square.data_ptr() == transposed.data_ptr()
    assert square.shape == transposed.shape
    assert square.stride() != transposed.stride()
    _attach(m)
    counts = _Counts(m)

    with torch.no_grad():
        a = m(x, torch.full((1,), 0.5), square)
        b = m(x, torch.full((1,), 0.5), transposed)

    assert counts.cap_embedder == 2
    assert not torch.allclose(a, b)


def test_an_inference_tensor_caption_still_caches():
    """The real path's conditioning IS inference tensors — the cache must not die.

    Native text encoders encode under ``inference_mode`` and the engine's cond
    move is a no-op ``.to()`` when device and dtype already match, so the DiT is
    handed inference tensors. They carry no version counter; treating that as
    unidentifiable would leave the cache dead in production while every test built
    on plain tensors kept passing.
    """
    with torch.inference_mode():
        ctx = _context(3)
    assert ctx.is_inference()
    assert tensor_identity(ctx) is not None

    m = _build()
    x = _latent(1, 16, 16)
    _attach(m)
    counts = _Counts(m)

    _run(m, x, [ctx])

    assert counts.cap_embedder == 1


# -- retention --------------------------------------------------------------


def test_the_model_retains_nothing_of_its_own_between_runs():
    """Every reused tensor hangs off the engine's run cache, never off the model.

    Clearing the cache (what ``NativeGenerator.sample`` does at run end and
    ``NativeModel.unload`` does on eviction) has to leave nothing behind.
    """
    m = _build()
    x = _latent(1, 16, 16)
    ctx = _context(3)
    cache, _ = _attach(m)
    _run(m, x, [ctx])
    assert len(cache) == 1

    cache.clear()
    assert len(cache) == 0
    retained = [k for k, v in vars(m).items() if isinstance(v, torch.Tensor)]
    assert retained == []

    counts = _Counts(m)
    _run(m, x, [ctx])
    assert counts.as_tuple() == (1, 1, 2)
