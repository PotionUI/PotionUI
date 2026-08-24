"""NaN/Inf watchdog: abort the sampling loop loudly instead of black images."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingNumericsError
from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.hooks import NumericsWatchdog

_NAN = float("nan")
_INF = float("inf")


def _const(v):
    return lambda x, sigma, cond: torch.full_like(x, v)


def _nan_from_call(n, bad=_NAN):
    """model_fn that returns finite zeros for the first ``n`` calls then ``bad``.

    One call per step (NoCFG), so the velocity — and hence ``x`` after that step —
    goes bad starting at step ``n``.
    """
    state = {"calls": 0}

    def model_fn(x, sigma, cond):
        i = state["calls"]
        state["calls"] += 1
        return torch.full_like(x, bad) if i >= n else torch.zeros_like(x)

    return model_fn


_SETTINGS = {"shift": 2.02, "guidance": None}


def _denoise(model, steps=6, **opts):
    return denoise(
        model, latents=torch.zeros(1, 4, 4, 4), cond={}, uncond=None, steps=steps,
        sampler_name=opts.pop("sampler_name", "euler"), sampling_settings=_SETTINGS,
        guidance_scale=0.0, seed_noise=torch.randn(1, 4, 4, 4),
        sampler_options=opts or None,
    )


# --- detection + message --------------------------------------------------

def test_nan_raises_with_step_and_sampler():
    # NaN velocity from step 2 -> x NaN after step 2; interval 1 checks every step.
    with pytest.raises(SamplingNumericsError) as ei:
        _denoise(_nan_from_call(2), nan_check_interval=1)
    err = ei.value
    assert err.step_index == 2
    assert err.sampler == "euler"
    assert "step 2" in str(err) and "euler" in str(err)


def test_inf_is_detected_too():
    with pytest.raises(SamplingNumericsError):
        _denoise(_nan_from_call(0, bad=_INF), nan_check_interval=1)


@pytest.mark.parametrize("sampler", ["euler", "dpmpp_2m", "euler_sde"])
def test_detects_across_sampler_families(sampler):
    with pytest.raises(SamplingNumericsError):
        _denoise(_const(_NAN), sampler_name=sampler, nan_check_interval=1)


# --- cadence (K) ----------------------------------------------------------

def test_cadence_only_checks_every_k_plus_edges():
    wd = NumericsWatchdog("euler", interval=4)
    ok, bad = torch.zeros(1, 4), torch.full((1, 4), _NAN)
    wd.on_step(0, 10, ok, 0.5, None)          # edge (step 0), finite -> fine
    wd.on_step(1, 10, bad, 0.5, None)         # not a check step -> ignored
    wd.on_step(2, 10, bad, 0.5, None)         # not a check step -> ignored
    with pytest.raises(SamplingNumericsError):
        wd.on_step(3, 10, bad, 0.5, None)     # (3+1)%4==0 -> checked -> raise


def test_final_step_always_checked():
    wd = NumericsWatchdog("euler", interval=1000)  # only edges get checked
    bad = torch.full((1, 4), _NAN)
    wd.on_step(5, 20, bad, 0.5, None)         # interior, not an edge -> ignored
    with pytest.raises(SamplingNumericsError):
        wd.on_step(19, 20, bad, 0.5, None)    # final step (total-1) -> checked


def test_interval_zero_disables():
    wd = NumericsWatchdog("euler", interval=0)
    bad = torch.full((1, 4), _NAN)
    wd.on_step(0, 10, bad, 0.5, None)         # disabled -> no raise
    wd.on_step(9, 10, bad, 0.5, None)


def test_denoise_interval_zero_completes_with_nan():
    # With the watchdog disabled a NaN run finishes (proving the option flows).
    out = _denoise(_const(_NAN), nan_check_interval=0)
    assert not torch.isfinite(out).all()


# --- read-only: clean runs stay byte-identical ---------------------------

def test_watchdog_does_not_mutate_clean_run():
    x = torch.randn(1, 4, 4, 4)
    sigmas = torch.tensor([1.0, 0.7, 0.4, 0.2, 0.0])
    with_wd = sample_euler(_const(1.3), x.clone(), sigmas, NoCFG(), {}, None,
                           hooks=[NumericsWatchdog("euler", 1)])
    without = sample_euler(_const(1.3), x.clone(), sigmas, NoCFG(), {}, None, hooks=[])
    assert torch.equal(with_wd, without)


def test_clean_denoise_unaffected():
    out = _denoise(_const(0.0), nan_check_interval=1)
    assert torch.isfinite(out).all()
