"""Tests for the ER-SDE-Solver-3 sampler (er_sde)."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled
from src.platform.runtime.native.sampling.algorithms.er_sde import sample_er_sde
from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.denoise_loop import (
    SAMPLERS,
    STOCHASTIC_SAMPLERS,
    denoise,
)


def _const(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)
    return model_fn


def _linear(a, b):
    def model_fn(x, sigma, cond):
        return a * x + b
    return model_fn


_SIGMAS = torch.tensor([1.0, 0.8, 0.6, 0.4, 0.2, 0.0])
# Same shape, but no step starts at sigma == 1, so the alpha floor never
# engages and the ODE degeneracy below is exact rather than O(1e-4).
_SIGMAS_INTERIOR = torch.tensor([0.95, 0.8, 0.6, 0.4, 0.2, 0.0])


def _run(model, x, sigmas=_SIGMAS, seed=7, **opts):
    if seed is not None:
        opts.setdefault("generator", torch.Generator().manual_seed(seed))
    return sample_er_sde(model, x.clone(), sigmas, NoCFG(), {}, None, sampler_options=opts)


def test_shape_and_finite():
    x = torch.randn(2, 3, 4)
    out = _run(_linear(0.2, 0.3), x)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()
    # The coefficients are float64 0-dim tensors; PyTorch promotion must keep
    # the latent in its own dtype rather than silently upcasting it.
    assert out.dtype == x.dtype


# --- identity noise scaler degenerates to the probability-flow ODE ---------
# phi(lambda) = lambda makes r_alpha*r == sigma_t/sigma_s and
# alpha_t*(1-r) == 1 - sigma_t/sigma_s, which is exactly the euler step; the
# diffusion radicand collapses to zero at the same time, so the run is also
# deterministic. See the module docstring for the algebra.

def test_identity_scaler_stage1_matches_euler():
    x = torch.randn(1, 5)
    er = _run(_linear(0.3, 0.1), x, sigmas=_SIGMAS_INTERIOR, noise_scaler="identity", max_stage=1)
    eul = sample_euler(_linear(0.3, 0.1), x.clone(), _SIGMAS_INTERIOR, NoCFG(), {}, None)
    assert torch.allclose(er, eul, atol=1e-5)


def test_identity_scaler_stage1_matches_euler_from_sigma_one():
    # The sigma == 1 step floors alpha, so the x0 coefficient carries an
    # O(_ALPHA_FLOOR) deviation there and nowhere else.
    x = torch.randn(1, 5)
    er = _run(_linear(0.3, 0.1), x, noise_scaler="identity", max_stage=1)
    eul = sample_euler(_linear(0.3, 0.1), x.clone(), _SIGMAS, NoCFG(), {}, None)
    assert torch.allclose(er, eul, atol=1e-3)


# --- determinism ----------------------------------------------------------

def test_generator_determinism():
    x = torch.randn(1, 6)
    a = _run(_linear(0.1, 0.2), x, seed=11)
    b = _run(_linear(0.1, 0.2), x, seed=11)
    c = _run(_linear(0.1, 0.2), x, seed=12)
    assert torch.equal(a, b)
    assert not torch.equal(a, c)


# --- solver order ---------------------------------------------------------

@pytest.mark.parametrize("max_stage", [1, 2, 3])
def test_max_stage_runs_finite(max_stage):
    out = _run(_linear(0.25, 0.4), torch.randn(1, 5), max_stage=max_stage)
    assert torch.isfinite(out).all()


def test_max_stage_changes_the_result():
    # Same seed for all three, so the noise draws coincide and the only
    # difference is the Taylor correction each stage adds.
    x = torch.randn(1, 5)
    outs = [_run(_linear(0.25, 0.4), x, max_stage=s) for s in (1, 2, 3)]
    assert not torch.equal(outs[0], outs[1])
    assert not torch.equal(outs[1], outs[2])


def test_invalid_max_stage_raises():
    with pytest.raises(ValueError):
        _run(_const(0.0), torch.zeros(1, 2), max_stage=4)


def test_unknown_noise_scaler_raises():
    with pytest.raises(ValueError):
        _run(_const(0.0), torch.zeros(1, 2), noise_scaler="nope")


# --- sigma == 1 does not blow up -----------------------------------------

@pytest.mark.parametrize("sigmas", [_SIGMAS, torch.tensor([1.0, 0.5, 0.0])])
def test_schedule_starting_at_one_is_finite(sigmas):
    out = _run(_linear(0.3, 0.2), torch.randn(2, 4), sigmas=sigmas)
    assert torch.isfinite(out).all()


def test_terminal_step_returns_x0():
    x = torch.tensor([[5.0]])
    out = _run(_const(2.0), x, sigmas=torch.tensor([1.0, 0.0]))
    assert torch.allclose(out, torch.tensor([[3.0]]))


# --- cancellation ---------------------------------------------------------

def test_cancellation_raises():
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 2

    with pytest.raises(SamplingCancelled):
        sample_er_sde(_const(1.0), torch.zeros(1, 3), _SIGMAS, NoCFG(), {}, None,
                      is_cancelled=cancel)


# --- registry + denoise() end to end --------------------------------------

def test_registered_as_stochastic_sampler():
    assert "er_sde" in SAMPLERS and callable(SAMPLERS["er_sde"])
    assert "er_sde" in STOCHASTIC_SAMPLERS


def test_denoise_runs_er_sde_end_to_end():
    latents = torch.zeros(1, 4, 8, 8)
    out = denoise(
        _const(2.0),
        latents,
        cond={},
        uncond=None,
        steps=4,
        sampler_name="er_sde",
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=torch.randn_like(latents),
    )
    assert out.shape == latents.shape
    assert torch.isfinite(out).all()
