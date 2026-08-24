"""FBCache integration on the Flux arch: probe/skip seam + byte-identical default.

Uses the same tiny-config builder as test_flux_model.py.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.arch.flux import model as flux_model
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_flux_model import TINY_FLUX2, _build_ready


def _forward(m, x, t, ctx, **kw):
    with torch.no_grad():
        return m(x, t, ctx, **kw)


# --- (a) None -> byte-identical to pre-change path ------------------------

def test_step_cache_none_is_byte_identical():
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    base = _forward(m, x, t, ctx)
    # explicit None must reproduce the no-kwarg output exactly.
    with_none = _forward(m, x, t, ctx, step_cache=None)
    assert torch.equal(base, with_none)


# --- (b) identical inputs -> second call skips + returns cached output ----

def test_identical_inputs_skip_returns_cached_output():
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    first = _forward(m, x, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    # identical inputs -> block-0 probe identical (rel==0) -> skip, reuse output.
    second = _forward(m, x, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


# --- (c) different-enough inputs -> no skip ------------------------------

def test_different_inputs_do_not_skip():
    m = _build_ready(TINY_FLUX2)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)

    _forward(m, torch.randn(1, 16, 16, 16), t, ctx, step_cache=cache)
    # a wholly different latent moves block-0 well past a 1% threshold -> compute.
    _forward(m, torch.randn(1, 16, 16, 16) * 5.0, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 2, "skipped": 0}


# --- (d) a skip actually avoids running the later blocks -----------------

def test_skip_avoids_later_blocks(monkeypatch):
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)

    calls = {"single": 0}
    # single_blocks run only AFTER the double-block probe; a skip must not reach them.
    orig = flux_model.SingleStreamBlock.forward

    def counting(self, *a, **k):
        calls["single"] += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(flux_model.SingleStreamBlock, "forward", counting)

    _forward(m, x, t, ctx, step_cache=cache)   # computes: single blocks run
    after_compute = calls["single"]
    assert after_compute > 0
    _forward(m, x, t, ctx, step_cache=cache)   # skips: single blocks must NOT run
    assert calls["single"] == after_compute


# --- ControlNet payload bypasses the cache entirely (roadmap S8/#8) -------

def test_control_payload_bypasses_step_cache():
    """FBCache's probe is captured at block 0; a ControlNet residual applied
    at a LATER block (control["input"][1:], control["output"]) is invisible
    to it. Rather than risk reusing a stale cached output under a changed
    residual, a control payload must bypass the cache entirely: the passed-in
    ``FirstBlockCache`` is never consulted or updated at all (stats stay at
    zero), even across repeated identical-input calls with a threshold that
    would otherwise always skip."""
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.99, warmup_steps=0)
    control = {}  # presence alone (not its content) triggers the bypass

    out1 = _forward(m, x, t, ctx, step_cache=cache, control=control)
    out2 = _forward(m, x, t, ctx, step_cache=cache, control=control)
    assert cache.stats() == {"computed": 0, "skipped": 0}
    # Real (non-cached) forwards both times, so the outputs still agree given
    # identical deterministic inputs — the bypass isn't a behavior change, only
    # a caching one.
    assert torch.equal(out1, out2)


def test_no_control_still_skips_as_before():
    """Sanity check that the control guard doesn't break the ordinary
    no-ControlNet path — same setup as test_control_payload_bypasses_step_cache
    but without ``control``, a skip must still happen."""
    m = _build_ready(TINY_FLUX2)
    x = torch.randn(1, 16, 16, 16)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.99, warmup_steps=0)

    _forward(m, x, t, ctx, step_cache=cache)
    _forward(m, x, t, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}


# --- shape change (resolution) resets, does not crash --------------------

def test_resolution_change_forces_compute():
    m = _build_ready(TINY_FLUX2)
    t = torch.tensor([0.5])
    ctx = torch.randn(1, 7, 32)
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)

    _forward(m, torch.randn(1, 16, 16, 16), t, ctx, step_cache=cache)
    # different spatial size -> probe shape differs -> compute, no reuse/crash.
    out = _forward(m, torch.randn(1, 16, 24, 16), t, ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0
    assert out.shape == (1, 16, 24, 16)
