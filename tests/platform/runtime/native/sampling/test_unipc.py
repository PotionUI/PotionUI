"""UniPC flow sampler: exactness, hooks, cancellation, multistep warmup."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled, SamplingNumericsError
from src.platform.runtime.native.sampling.algorithms import sample_euler, sample_unipc
from src.platform.runtime.native.sampling.algorithms.unipc import _build_R_b
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook, NumericsWatchdog


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


def test_constant_velocity_matches_euler_exactly():
    # Constant velocity -> exact/constant x0 -> D1 diffs and corrector vanish ->
    # first-order flow update is exact -> final x = x_init - v0 (sigma 1->0).
    x_init = torch.tensor([[4.0, -2.0]])
    sigmas = torch.tensor([1.0, 0.7, 0.4, 0.15, 0.0])
    out = sample_unipc(
        _const_velocity_model(3.0), x_init.clone(), sigmas, NoCFG(), {}, None
    )
    assert torch.allclose(out, x_init - 3.0, atol=1e-5)


def test_constant_velocity_exact_realistic_schedule():
    # Same, but on a real shifted schedule (exercises the warmup + corrector).
    from src.platform.runtime.native.sampling.flow_schedule import build_sigmas

    x_init = torch.randn(1, 4, 2, 2)
    sigmas = build_sigmas(8, shift=2.02)
    out = sample_unipc(
        _const_velocity_model(1.5), x_init.clone(), sigmas, NoCFG(), {}, None
    )
    assert torch.allclose(out, x_init - 1.5, atol=1e-4)


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.steps = 0
            self.starts = 0
            self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1
            assert denoised_x0 is not None

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.linspace(1.0, 0.0, 6)  # 5 steps
    sample_unipc(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter],
    )
    assert (counter.starts, counter.steps, counter.ends) == (1, 5, 1)


def test_cancellation_mid_loop():
    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] >= 3  # cancel at step index 2

    with pytest.raises(SamplingCancelled) as exc:
        sample_unipc(
            _const_velocity_model(1.0),
            torch.zeros(1, 2),
            torch.linspace(1.0, 0.0, 11),
            NoCFG(),
            {},
            None,
            is_cancelled=is_cancelled,
        )
    assert exc.value.step_index == 2


def test_solve_system_is_fp32_cpu_regardless_of_model_dtype():
    # Regression: cusolver has no bf16 LU factorization, so building R/b in the
    # model's (bf16, cuda) dtype/device makes torch.linalg.solve raise on GPU.
    # The order<=3 system is Python-float-sourced, so it must stay fp32 on CPU.
    R, b, _h_phi_1, _B_h = _build_R_b([0.5, 1.0], hh=-0.3, order=2, solver_type="bh2",
                                      device="cuda", dtype=torch.bfloat16)
    assert R.dtype == torch.float32 and b.dtype == torch.float32
    assert R.device.type == "cpu" and b.device.type == "cpu"


def test_order2_bf16_model_runs_without_cusolver_dtype_error():
    # A bf16 model exercises the corrector's linalg.solve at order 2; with the
    # fp32-CPU solve system it must run to a finite result (CPU proxy for the
    # CUDA-only cusolver bf16 failure this guards against).
    def model_fn(x, sigma, cond):
        return torch.full_like(x, 0.3)

    x = torch.randn(1, 4, 2, 2, dtype=torch.bfloat16)
    sigmas = torch.linspace(1.0, 0.0, 5).to(torch.bfloat16)  # 4 steps -> order-2 corrector runs
    out = sample_unipc(model_fn, x, sigmas, NoCFG(), {}, None)
    assert out.dtype == torch.bfloat16
    assert torch.isfinite(out.float()).all()


# -- discontinuity_steps: multistep history reset at an expert switch -------
# (Wan's _ExpertRouter swaps DiTs mid-run by dispatching on
# sigma; without a reset, unipc's corrector/predictor mix x0 estimates from
# the model BEFORE the switch with the model AFTER it.)

def _two_phase_model(v_before, v_after, switch_sigma):
    def model_fn(x, sigma, cond):
        sigma_val = float(sigma.reshape(-1)[0])
        v = v_before if sigma_val > switch_sigma else v_after
        return torch.full_like(x, v)

    return model_fn


def _split_pass_reference(sampler, model_fn, x_init, sigmas, switch):
    """Two INDEPENDENT sampler passes split exactly at the switch step (the
    ComfyUI-reference shape: a fresh sampler call per expert, no history
    crossing the boundary at all) -- the ground truth a single continuous
    run's discontinuity-reset is trying to reproduce without actually
    splitting the call."""
    first = sampler(model_fn, x_init.clone(), sigmas[:switch + 1], NoCFG(), {}, None)
    return sampler(model_fn, first, sigmas[switch:], NoCFG(), {}, None)


def test_euler_matches_split_pass_reference_exactly_no_history_to_poison():
    # Maintainer A/B: the SAME chain document is clean on euler and
    # NaNs on unipc. euler has no multistep state at all, so a single
    # continuous run across the discontinuity must land EXACTLY where two
    # independent passes split at the switch would -- there is nothing for
    # the model change to poison.
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])  # 4 steps
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sample_euler(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None)
    out_split = _split_pass_reference(sample_euler, model_fn, x_init, sigmas, switch)
    assert torch.allclose(out_continuous, out_split, atol=1e-6)


def test_unipc_without_reset_diverges_from_split_pass_reference():
    # The bug, isolated: a continuous unipc run with NO discontinuity_steps
    # (today's pre-fix behaviour) does NOT match the split-pass reference --
    # its corrector mixes an x0 estimate from the model before the switch
    # into the step after it, which two independent passes would never do.
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None)
    out_split = _split_pass_reference(sample_unipc, model_fn, x_init, sigmas, switch)
    assert not torch.allclose(out_continuous, out_split)


def test_unipc_with_reset_matches_split_pass_reference_exactly():
    # The fix: a continuous unipc run WITH discontinuity_steps={switch} must
    # reproduce the split-pass reference EXACTLY -- proving the reset makes
    # our single continuous engine numerically equivalent to ComfyUI's
    # two-pass-split-at-the-boundary reference shape, without actually
    # splitting the call (no hooks/progress/step-cache restructuring needed).
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    out_continuous = sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                                   sampler_options={"discontinuity_steps": {switch}})
    out_split = _split_pass_reference(sample_unipc, model_fn, x_init, sigmas, switch)
    assert torch.allclose(out_continuous, out_split, atol=1e-6)


def test_discontinuity_reset_is_a_noop_before_the_switch_step():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])  # 4 steps
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    captured = {"reset": [], "no_reset": []}

    class _Recorder(BaseStepHook):
        def __init__(self, key):
            self.key = key

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            captured[self.key].append(x.clone())

    sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                 hooks=[_Recorder("no_reset")])
    sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                 hooks=[_Recorder("reset")],
                 sampler_options={"discontinuity_steps": {switch}})

    for i in range(switch):
        assert torch.allclose(captured["no_reset"][i], captured["reset"][i]), i


def test_discontinuity_reset_diverges_from_unreset_run_at_the_switch_step():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])  # 4 steps
    switch = 2
    model_fn = _two_phase_model(1.5, -2.0, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    captured = {"reset": [], "no_reset": []}

    class _Recorder(BaseStepHook):
        def __init__(self, key):
            self.key = key

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            captured[self.key].append(x.clone())

    sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                 hooks=[_Recorder("no_reset")])
    sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                 hooks=[_Recorder("reset")],
                 sampler_options={"discontinuity_steps": {switch}})

    # Without the reset, the corrector at the switch step mixes the OTHER
    # model's x0 estimate into its linear extrapolation; with the reset that
    # corrector application is skipped there (treated as a fresh start).
    assert not torch.allclose(captured["no_reset"][switch], captured["reset"][switch])
    assert torch.isfinite(captured["reset"][switch]).all()


def test_discontinuity_reset_matches_order1_predictor_with_no_history():
    # After the reset at the switch step, the predictor there has an EMPTY
    # x0_prev (order forced to 1 by the freshly-zeroed lower_order_nums), so
    # it must equal the closed-form order-1 update: no D1 correction term.
    from src.platform.runtime.native.sampling.algorithms.unipc import _lambda

    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    switch = 2
    v_before, v_after = 1.5, -2.0
    model_fn = _two_phase_model(v_before, v_after, switch_sigma=float(sigmas[switch]))
    x_init = torch.randn(1, 4, 2, 2)

    x_entering_switch = {}

    class _CaptureEntry(BaseStepHook):
        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            if step_index == switch - 1:
                x_entering_switch["x"] = x.clone()

    out = {}

    class _CaptureAfter(BaseStepHook):
        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            if step_index == switch:
                out["x"] = x.clone()

    sample_unipc(model_fn, x_init.clone(), sigmas, NoCFG(), {}, None,
                 hooks=[_CaptureEntry(), _CaptureAfter()],
                 sampler_options={"discontinuity_steps": {switch}})

    x = x_entering_switch["x"]
    sigma = float(sigmas[switch])
    sigma_next = float(sigmas[switch + 1])
    x0 = x - sigma * v_after
    alpha_t, sigma_t = 1.0 - sigma_next, sigma_next
    alpha_s0, sigma_s0 = 1.0 - sigma, sigma
    h = _lambda(sigma_next) - _lambda(sigma)
    import math
    h_phi_1 = math.expm1(-h)
    expected = (sigma_t / sigma_s0) * x - alpha_t * h_phi_1 * x0
    assert torch.allclose(out["x"], expected, atol=1e-5)


def test_numerics_error_enriched_with_solver_order_and_history_depth():
    # A watchdog trip inside sample_unipc's own loop must come back with the
    # solver's OWN state at the moment of failure, not just the generic
    # step/sampler/attention the watchdog itself knows about.
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])  # 4 steps

    def nan_after_step2(x, sigma, cond):
        sigma_val = float(sigma.reshape(-1)[0])
        return torch.full_like(x, float("nan")) if sigma_val <= 0.5 else torch.zeros_like(x)

    x_init = torch.randn(1, 4, 2, 2)
    with pytest.raises(SamplingNumericsError) as exc:
        sample_unipc(nan_after_step2, x_init, sigmas, NoCFG(), {}, None,
                     hooks=[NumericsWatchdog("unipc", interval=1)])
    assert exc.value.solver_order in (1, 2)
    assert exc.value.history_depth >= 1


def test_no_nan_at_terminal_and_start_sigmas():
    # sigma0 == 1.0 (alpha 0, lambda -inf) and terminal 0 (lambda +inf) must not
    # produce NaN/Inf in the output.
    from src.platform.runtime.native.sampling.flow_schedule import build_sigmas

    x_init = torch.randn(1, 4, 2, 2)
    sigmas = build_sigmas(6, shift=2.02)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    out = sample_unipc(_const_velocity_model(0.7), x_init, sigmas, NoCFG(), {}, None)
    assert torch.isfinite(out).all()
