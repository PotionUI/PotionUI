"""Run-scoped reuse of Krea-2's text fusion and stream geometry.

The seam is ``Krea2._branch_inputs``, reached through the real ``forward`` and
the engine's real run scope (``NativeGenerator._run_cache``, which attaches a
``RunCache`` reading the DiT wrapper's effective revision live). What it holds
is the text fusion (``txtfusion`` + ``txtmlp``), the 3-axis position ids and the
joint ``[text; image]`` key-padding mask — all properties of the guidance branch
and the weights, never of the timestep or the noisy latent.

Coverage:
  (a) a cached run is bit-identical to an uncached one across timesteps, and
      the noisy tokens / timesteps stay live;
  (b) preparation happens once per branch, and two branches coexist in the run
      cache without evicting each other;
  (c) a changed text mask, reference grid or NAG context is never answered from
      an entry keyed on the previous one;
  (d) a value cached before a step-windowed LoRA edge cannot answer after it,
      driven through the real ``LoraStepWindowHook``;
  (e) the run scope releases the cache on failure/cancellation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeGenerator, NativeModel
from src.platform.runtime.native.lora.step_window import LoraStepWindow, LoraStepWindowHook
from vendor.gpl.comfyui.ops import pick_operations

# Mirrors test_krea2_model.py's TINY fixture: headdim 16 -> rope_axes [4,6,6].
TINY = {
    "image_model": "krea2", "features": 32, "heads": 2, "kvheads": 1,
    "channels": 4, "layers": 1, "multiplier": 1, "tdim": 16, "txtdim": 16,
    "txtheads": 2, "txtkvheads": 2, "txtlayers": 3, "patch": 2, "theta": 1000.0,
}


def _randomised_state_dict(module) -> dict[str, torch.Tensor]:
    sd: dict[str, torch.Tensor] = {}
    for k, v in module.state_dict().items():
        if k.endswith(".scale") or k.endswith(".mod.lin") or k.endswith(".modulation.lin"):
            sd[k] = torch.zeros_like(v)
        elif v.is_floating_point():
            sd[k] = torch.randn_like(v) * 0.02
        else:
            sd[k] = v.clone()
    return sd


def _build_ready(config=None) -> Krea2:
    torch.manual_seed(0)
    config = config or TINY
    m = Krea2.from_config(config, pick_operations(torch.float32, torch.float32))
    load_into_module(m, _randomised_state_dict(m), match_model_spec(config))
    m.eval()
    return m


def _inputs(seed=0, txt_len=5, neg_txt_len=5):
    g = torch.Generator().manual_seed(seed)
    x = torch.randn(1, 4, 8, 8, generator=g)
    te_hidden = torch.randn(1, txt_len, 3, 16, generator=g)
    neg_hidden = torch.randn(1, neg_txt_len, 3, 16, generator=g)
    return x, te_hidden, neg_hidden


def _run_scope(module):
    """The engine's own run scope, attached to a real ``NativeModel`` wrapper."""
    dit = NativeModel("diffusion_model", module)
    return dit, NativeGenerator._run_cache(SimpleNamespace(dit=dit))


class _Prepared:
    """Counts the two preparations the run cache is meant to hoist."""

    def __init__(self, module: Krea2) -> None:
        self.geometry = 0
        self.fusion = 0
        real_geometry, real_fusion = module._stream_geometry, module.prepare_context

        def geometry(*args, **kwargs):
            self.geometry += 1
            return real_geometry(*args, **kwargs)

        def fusion(*args, **kwargs):
            self.fusion += 1
            return real_fusion(*args, **kwargs)

        module._stream_geometry = geometry
        module.prepare_context = fusion


# --- (a) equivalence -------------------------------------------------------

SIGMAS = [0.9, 0.7, 0.45, 0.2]


def test_a_cached_run_is_bit_identical_across_timesteps():
    module = _build_ready()
    x, te_hidden, _ = _inputs()
    latents = [x * s for s in SIGMAS]

    uncached = [module(latent, torch.tensor([s]), te_hidden)
                for latent, s in zip(latents, SIGMAS)]

    _, scope = _run_scope(module)
    with scope:
        cached = [module(latent, torch.tensor([s]), te_hidden)
                  for latent, s in zip(latents, SIGMAS)]

    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)
    # the timestep and the noisy latent are live inputs, not cached ones
    assert not torch.equal(cached[0], cached[1])


def test_a_cached_run_with_references_is_bit_identical():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=3)
    ref = torch.randn(1, 4, 8, 8, generator=torch.Generator().manual_seed(9))
    mask = torch.tensor([[1, 1, 1, 1, 0]])

    uncached = [module(x * s, torch.tensor([s]), te_hidden, attention_mask=mask,
                       ref_latents=ref) for s in SIGMAS]

    _, scope = _run_scope(module)
    with scope:
        cached = [module(x * s, torch.tensor([s]), te_hidden, attention_mask=mask,
                         ref_latents=ref) for s in SIGMAS]

    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)


def test_a_cached_nag_run_is_bit_identical():
    module = _build_ready()
    x, te_hidden, neg_hidden = _inputs(seed=5, neg_txt_len=3)
    nag = {"scale": 2.0, "tau": 3.5, "alpha": 0.5}

    uncached = [module(x * s, torch.tensor([s]), te_hidden, nag_context=neg_hidden, nag=nag)
                for s in SIGMAS]

    _, scope = _run_scope(module)
    with scope:
        cached = [module(x * s, torch.tensor([s]), te_hidden, nag_context=neg_hidden, nag=nag)
                  for s in SIGMAS]

    for got, want in zip(cached, uncached):
        assert torch.equal(got, want)


# --- (b) preparation counts ------------------------------------------------

def test_preparation_runs_once_per_run_instead_of_once_per_step():
    module = _build_ready()
    x, te_hidden, _ = _inputs()

    without = _Prepared(module)
    for s in SIGMAS:
        module(x * s, torch.tensor([s]), te_hidden)
    assert (without.geometry, without.fusion) == (len(SIGMAS), len(SIGMAS))

    module = _build_ready()
    with_cache = _Prepared(module)
    _, scope = _run_scope(module)
    with scope:
        for s in SIGMAS:
            module(x * s, torch.tensor([s]), te_hidden)
    assert (with_cache.geometry, with_cache.fusion) == (1, 1)


def test_the_negative_prompt_fusion_is_prepared_once_alongside_the_positive():
    module = _build_ready()
    x, te_hidden, neg_hidden = _inputs(seed=5, neg_txt_len=3)
    counts = _Prepared(module)
    nag = {"scale": 2.0, "tau": 3.5, "alpha": 0.5}

    _, scope = _run_scope(module)
    with scope:
        for s in SIGMAS:
            module(x * s, torch.tensor([s]), te_hidden, nag_context=neg_hidden, nag=nag)

    assert (counts.geometry, counts.fusion) == (1, 2)


def test_two_guidance_branches_coexist_without_evicting_each_other():
    """A CFG run alternates cond/uncond forwards; neither may thrash the other."""
    module = _build_ready()
    x, cond, uncond = _inputs(seed=7, neg_txt_len=4)

    plain = [(module(x * s, torch.tensor([s]), cond),
              module(x * s, torch.tensor([s]), uncond)) for s in SIGMAS]

    module_c = _build_ready()
    counts = _Prepared(module_c)
    _, scope = _run_scope(module_c)
    with scope:
        cached = [(module_c(x * s, torch.tensor([s]), cond),
                   module_c(x * s, torch.tensor([s]), uncond)) for s in SIGMAS]
        assert len(module_c.run_cache) == 2

    assert (counts.geometry, counts.fusion) == (2, 2)
    for (got_c, got_u), (want_c, want_u) in zip(cached, plain):
        assert torch.equal(got_c, want_c)
        assert torch.equal(got_u, want_u)


# --- (c) key components ----------------------------------------------------

def test_a_changed_text_mask_is_never_answered_from_the_cache():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=11)
    full = torch.tensor([[1, 1, 1, 1, 1]])
    padded = torch.tensor([[1, 1, 1, 0, 0]])
    t = torch.tensor([0.5])

    want_full = module(x, t, te_hidden, attention_mask=full)
    want_padded = module(x, t, te_hidden, attention_mask=padded)
    assert not torch.equal(want_full, want_padded)

    counts = _Prepared(module)
    _, scope = _run_scope(module)
    with scope:
        assert torch.equal(module(x, t, te_hidden, attention_mask=full), want_full)
        assert torch.equal(module(x, t, te_hidden, attention_mask=padded), want_padded)
    assert (counts.geometry, counts.fusion) == (2, 2)


def test_a_changed_reference_grid_is_never_answered_from_the_cache():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=13)
    g = torch.Generator().manual_seed(17)
    big = torch.randn(1, 4, 8, 8, generator=g)
    small = torch.randn(1, 4, 4, 4, generator=g)
    t = torch.tensor([0.5])

    want_big = module(x, t, te_hidden, ref_latents=big)
    want_small = module(x, t, te_hidden, ref_latents=small)
    assert not torch.equal(want_big, want_small)

    counts = _Prepared(module)
    _, scope = _run_scope(module)
    with scope:
        assert torch.equal(module(x, t, te_hidden, ref_latents=big), want_big)
        assert torch.equal(module(x, t, te_hidden, ref_latents=small), want_small)
    assert counts.geometry == 2


def test_a_changed_nag_context_is_never_answered_from_the_cache():
    module = _build_ready()
    x, te_hidden, neg_a = _inputs(seed=19)
    neg_b = torch.randn(1, 5, 3, 16, generator=torch.Generator().manual_seed(23))
    nag = {"scale": 2.0, "tau": 3.5, "alpha": 0.5}
    t = torch.tensor([0.5])

    want_a = module(x, t, te_hidden, nag_context=neg_a, nag=nag)
    want_b = module(x, t, te_hidden, nag_context=neg_b, nag=nag)
    assert not torch.equal(want_a, want_b)

    _, scope = _run_scope(module)
    with scope:
        assert torch.equal(module(x, t, te_hidden, nag_context=neg_a, nag=nag), want_a)
        assert torch.equal(module(x, t, te_hidden, nag_context=neg_b, nag=nag), want_b)


# --- (d) step-windowed LoRA edge -------------------------------------------

def _txtmlp_lora(rank=4, seed=1, scale=0.4):
    """A kohya LoRA over ``txtmlp.1`` — a Linear the TEXT FUSION runs through,
    so a stale cached fusion is a wrong answer, not merely an old one."""
    g = torch.Generator().manual_seed(seed)
    stem = "lora_unet_txtmlp_1"
    return {
        f"{stem}.lora_up.weight": torch.randn(32, rank, generator=g) * scale,
        f"{stem}.lora_down.weight": torch.randn(rank, 16, generator=g) * scale,
        f"{stem}.alpha": torch.tensor(float(rank)),
    }


def test_a_cached_fusion_cannot_survive_a_lora_window_edge():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=29)
    t = torch.tensor([0.5])
    lora = _txtmlp_lora()

    dit, scope = _run_scope(module)
    hook = LoraStepWindowHook(dit, [(lora, 1.0, LoraStepWindow(3, 4))])
    counts = _Prepared(module)
    try:
        with scope:
            hook.on_start(6)
            unpatched = module(x, t, te_hidden)
            assert torch.equal(module(x, t, te_hidden), unpatched)
            assert (counts.geometry, counts.fusion) == (1, 1)

            hook.on_step(1, 6, None, 0.0, None)  # entering step 2 (0-based): window opens
            patched = module(x, t, te_hidden)
            assert counts.fusion == 2, "the window applied: the cached fusion must not answer"
            assert not torch.equal(patched, unpatched)

            hook.on_step(3, 6, None, 0.0, None)  # entering step 4: window closes
            restored = module(x, t, te_hidden)
            assert counts.fusion == 3
            assert torch.allclose(restored, unpatched, atol=1e-6)
    finally:
        hook.close()


# --- (e) lifetime ----------------------------------------------------------

def test_the_run_scope_releases_the_cache_on_cancellation():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=31)
    _, scope = _run_scope(module)

    with pytest.raises(RuntimeError, match="cancelled"):
        with scope:
            module(x, torch.tensor([0.5]), te_hidden)
            cache = module.run_cache
            assert len(cache) == 1
            raise RuntimeError("cancelled")

    assert module.run_cache is None
    assert len(cache) == 0


def test_without_a_run_cache_the_forward_is_unchanged():
    module = _build_ready()
    x, te_hidden, _ = _inputs(seed=37)
    assert getattr(module, "run_cache", None) is None
    out = module(x, torch.tensor([0.5]), te_hidden)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
