"""Tests for the curated sampler batch: dpmpp_2m_sde, dpmpp_3m, res_multistep, lcm."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.dpmpp_2m_sde import sample_dpmpp_2m_sde
from src.platform.runtime.native.sampling.algorithms.dpmpp_3m import sample_dpmpp_3m
from src.platform.runtime.native.sampling.algorithms.dpmpp_flow import sample_dpmpp_2m
from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.lcm import sample_lcm
from src.platform.runtime.native.sampling.algorithms.res_multistep import sample_res_multistep
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.denoise_loop import SAMPLERS, denoise
from src.platform.runtime.native.sampling.hooks import BaseStepHook
from src.platform.runtime.native.errors import SamplingCancelled


def _const(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)
    return model_fn


def _linear(a, b):
    # x-dependent deterministic velocity so x0 genuinely evolves step-to-step
    # (multistep corrections engage; not a degenerate constant trajectory).
    def model_fn(x, sigma, cond):
        return a * x + b
    return model_fn


_SIGMAS = torch.tensor([1.0, 0.8, 0.6, 0.4, 0.2, 0.0])


def _run(sampler, model, x, sigmas=_SIGMAS, **opts):
    return sampler(model, x.clone(), sigmas, NoCFG(), {}, None,
                   sampler_options=opts or None)


# --- shape / finiteness (all four) ---------------------------------------

@pytest.mark.parametrize("sampler", [sample_dpmpp_2m_sde, sample_dpmpp_3m, sample_res_multistep, sample_lcm])
def test_shape_and_finite(sampler):
    x = torch.randn(2, 3, 4)
    out = _run(sampler, _linear(0.2, 0.3), x)
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# --- fixed-generator determinism (stochastic samplers) -------------------

@pytest.mark.parametrize("sampler", [sample_dpmpp_2m_sde, sample_lcm])
def test_generator_determinism(sampler):
    x = torch.randn(1, 6)
    a = sampler(_linear(0.1, 0.2), x.clone(), _SIGMAS, NoCFG(), {}, None,
                sampler_options={"generator": torch.Generator().manual_seed(11)})
    b = sampler(_linear(0.1, 0.2), x.clone(), _SIGMAS, NoCFG(), {}, None,
                sampler_options={"generator": torch.Generator().manual_seed(11)})
    c = sampler(_linear(0.1, 0.2), x.clone(), _SIGMAS, NoCFG(), {}, None,
                sampler_options={"generator": torch.Generator().manual_seed(12)})
    assert torch.equal(a, b)      # same seed -> identical
    assert not torch.equal(a, c)  # different seed -> different


# --- dpmpp_2m_sde: eta=0 relationship to plain dpmpp_2m ------------------

def test_2m_sde_eta0_matches_dpmpp_2m_allclose():
    # Analytically identical (midpoint), float-order aside -> allclose on a
    # varying stub.
    x = torch.randn(1, 5)
    sde = _run(sample_dpmpp_2m_sde, _linear(0.3, 0.1), x, eta=0.0)
    det = sample_dpmpp_2m(_linear(0.3, 0.1), x.clone(), _SIGMAS, NoCFG(), {}, None)
    assert torch.allclose(sde, det, atol=1e-5)


def test_2m_sde_eta0_constant_velocity_matches_dpmpp_2m_and_euler():
    # Constant velocity -> midpoint correction is exactly 0 and the noise
    # coefficient is 0, so eta=0 is the plain 2M drift == the euler step
    # algebraically. Not BIT-exact: the drift coefficient is ``ratio**(1+eta)``,
    # and torch.pow(ratio, 1.0) differs from ratio by a ULP, so the genuine
    # relationship is allclose (documented, contra a bit-identity claim).
    x = torch.randn(1, 5)
    sde = _run(sample_dpmpp_2m_sde, _const(1.3), x, eta=0.0)
    det = sample_dpmpp_2m(_const(1.3), x.clone(), _SIGMAS, NoCFG(), {}, None)
    eul = sample_euler(_const(1.3), x.clone(), _SIGMAS, NoCFG(), {}, None)
    assert torch.allclose(sde, det, atol=1e-6)
    assert torch.allclose(sde, eul, atol=1e-6)


def test_2m_sde_invalid_eta_raises():
    with pytest.raises(ValueError):
        _run(sample_dpmpp_2m_sde, _const(0.0), torch.zeros(1, 2), eta=1.5)


# --- dpmpp_3m: warmup degeneracy + constant-velocity collapse ------------

def test_3m_two_step_matches_dpmpp_2m():
    # A 2-step run never reaches 3rd order (step0 first-order, step1 terminal),
    # so it matches dpmpp_2m where the math says it must.
    sig2 = torch.tensor([1.0, 0.5, 0.0])
    x = torch.randn(1, 5)
    three = sample_dpmpp_3m(_linear(0.2, 0.4), x.clone(), sig2, NoCFG(), {}, None)
    two = sample_dpmpp_2m(_linear(0.2, 0.4), x.clone(), sig2, NoCFG(), {}, None)
    assert torch.allclose(three, two, atol=1e-6)


def test_3m_constant_velocity_collapses_to_euler():
    x = torch.randn(1, 5)
    three = sample_dpmpp_3m(_const(0.9), x.clone(), _SIGMAS, NoCFG(), {}, None)
    eul = sample_euler(_const(0.9), x.clone(), _SIGMAS, NoCFG(), {}, None)
    assert torch.allclose(three, eul, atol=1e-6)


# --- res_multistep: constant-velocity collapse + 2nd-order agreement -----

def test_res_constant_velocity_collapses_to_euler():
    x = torch.randn(1, 5)
    res = sample_res_multistep(_const(1.1), x.clone(), _SIGMAS, NoCFG(), {}, None)
    eul = sample_euler(_const(1.1), x.clone(), _SIGMAS, NoCFG(), {}, None)
    assert torch.allclose(res, eul, atol=1e-6)


def test_res_two_step_first_order_matches_euler_first_step():
    # 2-step: step0 first-order (no history), step1 terminal -> equals dpmpp_2m.
    sig2 = torch.tensor([1.0, 0.5, 0.0])
    x = torch.randn(1, 5)
    res = sample_res_multistep(_linear(0.2, 0.4), x.clone(), sig2, NoCFG(), {}, None)
    two = sample_dpmpp_2m(_linear(0.2, 0.4), x.clone(), sig2, NoCFG(), {}, None)
    assert torch.allclose(res, two, atol=1e-6)


# --- lcm: terminal returns x0 --------------------------------------------

def test_lcm_terminal_returns_x0():
    # Single (terminal) step: x_final == x0 == x - sigma*v, deterministically.
    x = torch.tensor([[5.0]])
    out = sample_lcm(_const(2.0), x.clone(), torch.tensor([1.0, 0.0]), NoCFG(), {}, None)
    assert torch.allclose(out, torch.tensor([[3.0]]))  # 5 - 1*2


# --- cancellation + hooks (representative across the batch) ---------------

@pytest.mark.parametrize("sampler", [sample_dpmpp_2m_sde, sample_dpmpp_3m, sample_res_multistep, sample_lcm])
def test_cancellation_raises(sampler):
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 2  # cancel on the 2nd poll

    with pytest.raises(SamplingCancelled):
        sampler(_const(1.0), torch.zeros(1, 3), _SIGMAS, NoCFG(), {}, None, is_cancelled=cancel)


@pytest.mark.parametrize("sampler", [sample_dpmpp_2m_sde, sample_dpmpp_3m, sample_res_multistep, sample_lcm])
def test_hooks_fire_once_per_step(sampler):
    class Counter(BaseStepHook):
        def __init__(self):
            self.starts = self.steps = self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1
            self.total = total_steps

        def on_step(self, *_):
            self.steps += 1

        def on_end(self):
            self.ends += 1

    c = Counter()
    sampler(_const(1.0), torch.zeros(1, 2), torch.tensor([1.0, 0.5, 0.0]), NoCFG(), {}, None, hooks=[c])
    assert (c.starts, c.steps, c.total, c.ends) == (1, 2, 2, 1)


# --- registry + warm-start exclusion --------------------------------------

def test_all_new_samplers_registered():
    for name in ("dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "lcm"):
        assert name in SAMPLERS and callable(SAMPLERS[name])


@pytest.mark.parametrize("name", ["dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "lcm"])
def test_warm_start_resume_excludes_new_samplers(name):
    # Trajectory warm-start is euler-only; denoise must reject a resume request
    # for any of the new samplers (the engine gates on euler; this is the net).
    with pytest.raises(ValueError, match="euler"):
        denoise(
            _const(0.0), latents=torch.zeros(1, 4), cond={}, steps=4,
            sampler_name=name, sampling_settings={"shift": 2.02, "guidance": None},
            guidance_scale=0.0, resume=(2, torch.zeros(1, 4)),
        )


# -- discontinuity_steps: multistep history reset at an expert switch -------
# (The same mechanism unipc already had, wired into every OTHER
# stateful multistep sampler -- dpmpp_2m_sde, dpmpp_3m, res_multistep. lcm and
# the euler variants carry no cross-step history and need nothing.)

def _two_phase_model(v_before, v_after, switch_sigma):
    def model_fn(x, sigma, cond):
        sigma_val = float(sigma.reshape(-1)[0])
        v = v_before if sigma_val > switch_sigma else v_after
        return torch.full_like(x, v)

    return model_fn


# (sampler, fixed sampler_options merged with discontinuity_steps at call time)
# dpmpp_2m_sde is pinned to eta=0 so the run is deterministic -- the reset
# mechanism is about the extrapolation history, not the SDE noise draw.
_DISCONTINUITY_CASES = [
    (sample_dpmpp_2m_sde, {"eta": 0.0}),
    (sample_dpmpp_3m, {}),
    (sample_res_multistep, {}),
]


def _split_pass_reference(sampler, base_opts, model_fn, x_init, sigmas, switch):
    first = sampler(model_fn, x_init.clone(), sigmas[:switch + 1], NoCFG(), {}, None,
                     sampler_options=base_opts or None)
    return sampler(model_fn, first, sigmas[switch:], NoCFG(), {}, None,
                    sampler_options=base_opts or None)


@pytest.mark.parametrize("sampler,base_opts", _DISCONTINUITY_CASES)
def test_without_reset_diverges_from_split_pass_reference(sampler, base_opts):
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sampler(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                              sampler_options=base_opts or None)
    out_split = _split_pass_reference(sampler, base_opts, model_fn, x_init, sigmas, switch)
    assert not torch.allclose(out_continuous, out_split)


@pytest.mark.parametrize("sampler,base_opts", _DISCONTINUITY_CASES)
def test_with_reset_matches_split_pass_reference_exactly(sampler, base_opts):
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sampler(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                              sampler_options={**base_opts, "discontinuity_steps": {switch}})
    out_split = _split_pass_reference(sampler, base_opts, model_fn, x_init, sigmas, switch)
    assert torch.allclose(out_continuous, out_split, atol=1e-6)


@pytest.mark.parametrize("sampler,base_opts", _DISCONTINUITY_CASES)
def test_discontinuity_reset_is_a_noop_before_the_switch_step(sampler, base_opts):
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    captured = {"reset": [], "no_reset": []}

    class _Recorder(BaseStepHook):
        def __init__(self, key):
            self.key = key

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            captured[self.key].append(x.clone())

    sampler(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
            hooks=[_Recorder("no_reset")], sampler_options=base_opts or None)
    sampler(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
            hooks=[_Recorder("reset")],
            sampler_options={**base_opts, "discontinuity_steps": {switch}})

    for i in range(switch):
        assert torch.allclose(captured["no_reset"][i], captured["reset"][i]), i
