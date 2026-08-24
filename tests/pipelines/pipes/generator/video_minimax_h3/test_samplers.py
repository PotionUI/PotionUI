"""Numeric tests for MiniMax-H3's per-stream steppers.

Every sampler is checked against an ANALYTIC trajectory rather than against a
recorded output, on two fields whose exact solution is known in closed form
(both derived here from `dx/dsigma = (x - D)/sigma`, the relation the module
docstring in `samplers.py` integrates):

* constant model velocity -- `D` is constant along the trajectory, so every
  order of solver is exact and all three must land on the same endpoint;
* `D(sigma) = a + b*sigma^2` -- genuinely curved, so the second-order
  samplers must beat Euler. The ORDERING is asserted, not a magic error.

CPU-only, no model involved: a "model" here is a closure that returns the
velocity which makes `data_estimate` produce the `D` the field prescribes.
"""

from __future__ import annotations

import math

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.samplers import (
    DPMPP_2M,
    ER_SDE,
    EULER,
    RES_MULTISTEP,
    SA_SOLVER,
    SAMPLERS,
    DPMPlusPlus2MStepper,
    ERSDEStepper,
    EulerStepper,
    ResMultistepStepper,
    SASolverStepper,
    make_stepper,
)
from src.pipelines.pipes.generator.video_minimax_h3.schedule import (
    AUDIO_SHIFT,
    VIDEO_SHIFT,
    build_sigma_schedule,
    data_estimate,
    euler_step,
)


def _velocity(denoised: torch.Tensor, sample: torch.Tensor, timestep: float) -> torch.Tensor:
    """The model output that makes `data_estimate` return exactly `denoised`.

    Built from `1 - timestep` (not from the grid sigma) because that is the
    quantity `data_estimate` recomputes -- otherwise the float32 round trip
    the reference deliberately keeps would show up as field error here.
    """
    return (denoised - sample) / (1.0 - timestep)


_DETERMINISTIC_SAMPLERS = tuple(sampler for sampler in SAMPLERS if sampler != ER_SDE)


def _run(sampler: str, sigmas, denoised_fn, x_start: torch.Tensor, *, generator=None) -> torch.Tensor:
    stepper = make_stepper(sampler, generator=generator or torch.Generator().manual_seed(0))
    x = x_start.clone()
    for index in range(len(sigmas) - 1):
        sigma = float(sigmas[index])
        timestep = 1.0 - sigma
        model_output = _velocity(denoised_fn(sigma, x), x, timestep)
        x = stepper.step(model_output, timestep, x, sigma, float(sigmas[index + 1]))
    return x


# -- constant velocity: exact for every DETERMINISTIC order --------------------
# `er_sde` is excluded here -- it injects noise every non-terminal step by
# design, so it cannot land exactly on the analytic endpoint; see its own
# reproducibility/variance tests below instead.

@pytest.mark.parametrize("sampler", _DETERMINISTIC_SAMPLERS)
def test_constant_velocity_field_lands_on_the_analytic_endpoint(sampler):
    """`x0 = x_t + sigma*v` with `v` constant makes `D` constant along the
    trajectory, so the exact solution is one Euler step: `x(0) = x_start +
    sigma_start*v`. No solver may miss it."""
    velocity = torch.tensor([0.5, -1.25, 2.0])
    x_start = torch.tensor([1.0, -0.5, 0.25])
    sigmas = [1.0, 0.82, 0.6, 0.41, 0.2, 0.07, 0.0]

    got = _run(sampler, sigmas, lambda sigma, x: x + sigma * velocity, x_start)
    analytic = x_start + sigmas[0] * velocity
    torch.testing.assert_close(got, analytic, rtol=0, atol=2e-6)


# -- curved trajectory: the second-order samplers must win ----------------------

_A = torch.tensor([0.4, -0.9, 1.5])
_B = torch.tensor([1.3, 0.7, -2.1])

_LOG_HEAD = math.log(0.95)
_LOG_TAIL = math.log(0.04)


def _curved_denoised(sigma: float, _x: torch.Tensor) -> torch.Tensor:
    return _A + _B * (sigma ** 2)


def _curved_analytic(sigma: float, *, sigma_start: float, x_start: torch.Tensor) -> torch.Tensor:
    """Closed form of `dx/dsigma = (x - D)/sigma` for `D = a + b*sigma^2`.

    Substituting `u = x/sigma` gives `du/dsigma = -D/sigma^2`, which
    integrates to `u(s) = u(s0) + a/s - a/s0 + b*(s0 - s)`; multiplying back
    by `s` gives the expression below.
    """
    return (
        sigma * x_start / sigma_start + _A - _A * sigma / sigma_start + _B * sigma * (sigma_start - sigma)
    )


def _curved_errors(sigmas) -> dict:
    x_start = torch.tensor([0.2, 0.6, -1.1])
    analytic = _curved_analytic(float(sigmas[-1]), sigma_start=float(sigmas[0]), x_start=x_start)
    return {
        sampler: float((_run(sampler, sigmas, _curved_denoised, x_start) - analytic).abs().max())
        for sampler in SAMPLERS
    }


def _curved_errors_deterministic_only(sigmas) -> dict:
    """`_curved_errors`, minus `er_sde` -- its injected noise makes a single
    trajectory's error meaningless without averaging over seeds, which the
    order/refinement tests below have no need for since they only compare
    the DETERMINISTIC samplers against Euler."""
    return {sampler: error for sampler, error in _curved_errors(sigmas).items() if sampler != ER_SDE}


@pytest.mark.parametrize("shift", (VIDEO_SHIFT, AUDIO_SHIFT))
@pytest.mark.parametrize("steps", (4, 8, 16, 30))
def test_second_order_samplers_beat_euler_on_the_real_h3_schedules(steps, shift):
    """The grids that matter: both streams' shifted schedules, at step counts
    from the turbo profile up. The terminal knot is dropped because a step
    into `sigma_next == 0` is first-order in every sampler by construction,
    so keeping it would dilute the comparison with an identical last step."""
    errors = _curved_errors_deterministic_only(build_sigma_schedule(steps, shift).sigmas.tolist()[:-1])
    assert errors[EULER] > 1e-3, errors
    assert errors[RES_MULTISTEP] < errors[EULER], errors
    assert errors[DPMPP_2M] < errors[EULER], errors
    assert errors[SA_SOLVER] < errors[EULER], errors


@pytest.mark.parametrize("sampler", (RES_MULTISTEP, DPMPP_2M, SA_SOLVER))
def test_the_second_order_advantage_grows_as_the_grid_refines(sampler):
    """Euler's error falls like `h`, a second-order sampler's like `h^2`, so
    halving `h` must roughly DOUBLE the ratio between them.

    Asserted on a geometric grid (uniform in `log sigma`, the variable the
    solvers integrate in) so `h` halves cleanly with each refinement. The
    advantage shrinks in the other direction, on coarse grids, where the
    linear extrapolation through `D_prev` reaches far outside the interval
    it was fitted on -- which is why the assertion runs this way round.
    """
    ratios = []
    for count in (13, 25, 49, 97):
        errors = _curved_errors(torch.exp(torch.linspace(_LOG_HEAD, _LOG_TAIL, count)).tolist())
        ratios.append(errors[EULER] / errors[sampler])
    assert ratios == sorted(ratios), ratios
    assert ratios[-1] > 4.0 * ratios[0], ratios


def test_bite_check_a_history_less_multistep_is_exactly_euler():
    """BITE CHECK for both tests above: if the second-order term were dead,
    the two samplers would be Euler. Reconstructing them with the history
    dropped every step must reproduce Euler's trajectory bit for bit -- and
    the intact steppers must NOT."""
    sigmas = [0.95, 0.8, 0.62, 0.45, 0.28, 0.12, 0.04]
    x_start = torch.tensor([0.2, 0.6, -1.1])

    for sampler in (RES_MULTISTEP, DPMPP_2M):
        x = x_start.clone()
        for index in range(len(sigmas) - 1):
            sigma = float(sigmas[index])
            timestep = 1.0 - sigma
            model_output = _velocity(_curved_denoised(sigma, x), x, timestep)
            x = make_stepper(sampler).step(model_output, timestep, x, sigma, float(sigmas[index + 1]))

        euler = _run(EULER, sigmas, _curved_denoised, x_start)
        torch.testing.assert_close(x, euler, rtol=0, atol=0)
        assert not torch.allclose(_run(sampler, sigmas, _curved_denoised, x_start), euler)


# -- step-level contracts -------------------------------------------------------

def test_euler_stepper_is_the_reference_euler_step():
    velocity = torch.tensor([0.3, -0.7])
    sample = torch.tensor([1.0, 2.0])
    got = EulerStepper().step(velocity, 0.4, sample, 0.6, 0.3)
    torch.testing.assert_close(got, euler_step(velocity, 0.4, sample, 0.6, 0.3), rtol=0, atol=0)


@pytest.mark.parametrize("sampler", (RES_MULTISTEP, DPMPP_2M, SA_SOLVER))
def test_the_first_step_of_a_multistep_sampler_is_the_euler_step(sampler):
    velocity = torch.tensor([0.3, -0.7])
    sample = torch.tensor([1.0, 2.0])
    got = make_stepper(sampler).step(velocity, 0.4, sample, 0.6, 0.3)
    torch.testing.assert_close(got, euler_step(velocity, 0.4, sample, 0.6, 0.3), rtol=0, atol=0)


@pytest.mark.parametrize("sampler", SAMPLERS)
def test_the_terminal_step_lands_exactly_on_the_data_estimate(sampler):
    """`sigma_next == 0` makes `h` infinite, so it is first-order in every
    sampler -- and first order at ratio 0 is the x0 estimate itself. `er_sde`'s
    noise term is also exactly 0 there (`noise_std = sigma_next * ... == 0`),
    so it is checked for BIT-EXACT equality here too, not just "close"."""
    stepper = make_stepper(sampler, generator=torch.Generator().manual_seed(0))
    velocity = torch.tensor([0.3, -0.7])
    sample = torch.tensor([1.0, 2.0])
    stepper.step(velocity, 0.2, sample, 0.8, 0.5)   # prime the history

    got = stepper.step(velocity, 0.9, sample, 0.1, 0.0)
    torch.testing.assert_close(got, data_estimate(velocity, 0.9, sample), rtol=0, atol=1e-6)


@pytest.mark.parametrize("sampler", (RES_MULTISTEP, DPMPP_2M, SA_SOLVER))
def test_history_is_per_stepper_so_two_streams_do_not_share_it(sampler):
    """Video and audio walk DIFFERENT grids through one transformer call, so
    interleaving two steppers must give what running each alone gives."""
    video_sigmas = [0.9, 0.6, 0.3, 0.1]
    audio_sigmas = [0.7, 0.5, 0.24, 0.05]
    x_video = torch.tensor([0.5, -0.2, 0.3])
    x_audio = torch.tensor([-1.0, 0.8, -0.4])

    video_stepper, audio_stepper = make_stepper(sampler), make_stepper(sampler)
    interleaved_video, interleaved_audio = x_video.clone(), x_audio.clone()
    for index in range(len(video_sigmas) - 1):
        for stepper, sigmas, holder in (
            (video_stepper, video_sigmas, "video"), (audio_stepper, audio_sigmas, "audio"),
        ):
            sample = interleaved_video if holder == "video" else interleaved_audio
            sigma = float(sigmas[index])
            timestep = 1.0 - sigma
            stepped = stepper.step(
                _velocity(_curved_denoised(sigma, sample), sample, timestep), timestep, sample,
                sigma, float(sigmas[index + 1]),
            )
            if holder == "video":
                interleaved_video = stepped
            else:
                interleaved_audio = stepped

    torch.testing.assert_close(
        interleaved_video, _run(sampler, video_sigmas, _curved_denoised, x_video), rtol=0, atol=0)
    torch.testing.assert_close(
        interleaved_audio, _run(sampler, audio_sigmas, _curved_denoised, x_audio), rtol=0, atol=0)


def test_make_stepper_hands_out_a_fresh_object_every_call():
    first, second = make_stepper(RES_MULTISTEP), make_stepper(RES_MULTISTEP)
    assert first is not second
    assert isinstance(first, ResMultistepStepper)
    assert isinstance(make_stepper(DPMPP_2M), DPMPlusPlus2MStepper)
    assert isinstance(make_stepper(EULER), EulerStepper)
    assert isinstance(make_stepper(SA_SOLVER), SASolverStepper)

    gen = torch.Generator().manual_seed(0)
    er_sde_first, er_sde_second = make_stepper(ER_SDE, generator=gen), make_stepper(ER_SDE, generator=gen)
    assert er_sde_first is not er_sde_second
    assert isinstance(er_sde_first, ERSDEStepper)


def test_make_stepper_rejects_an_unknown_sampler():
    with pytest.raises(ValueError, match="sampler must be one of"):
        make_stepper("heun")


def test_make_stepper_refuses_er_sde_without_a_generator():
    with pytest.raises(ValueError, match="needs a seeded torch.Generator"):
        make_stepper(ER_SDE)


@pytest.mark.parametrize("sampler", (RES_MULTISTEP, DPMPP_2M, SA_SOLVER, ER_SDE))
def test_half_precision_samples_keep_their_dtype(sampler):
    stepper = make_stepper(sampler, generator=torch.Generator().manual_seed(0))
    sample = torch.tensor([1.0, 2.0], dtype=torch.bfloat16)
    velocity = torch.tensor([0.3, -0.7], dtype=torch.bfloat16)
    stepper.step(velocity, 0.4, sample, 0.6, 0.3)
    assert stepper.step(velocity, 0.7, sample, 0.3, 0.1).dtype == torch.bfloat16


# -- er_sde: noise-scale limit, reproducibility, variance -----------------------

def test_er_sde_at_eta_zero_matches_res_multistep_bit_for_bit():
    """CONVERGENCE-ORDER PIN for `er_sde`: at `eta=0` the module docstring's
    derivation collapses the drift to bit-for-bit `res_multistep`'s own
    formula (`ratio_eta -> ratio`, `b_eta -> h - (1-ratio)`) AND the noise
    term to exactly 0 -- so an `eta=0` `ERSDEStepper` must reproduce
    `res_multistep`'s ALREADY-VERIFIED second-order trajectory exactly,
    seed or no seed. This is what "recovers its deterministic order" means
    for a stochastic sampler: not a looser convergence-rate estimate, but
    equality with a solver whose order is already pinned elsewhere in this
    file."""
    sigmas = [0.95, 0.8, 0.62, 0.45, 0.28, 0.12, 0.04]
    x_start = torch.tensor([0.2, 0.6, -1.1])

    zero_eta = ERSDEStepper(torch.Generator().manual_seed(0), eta=0.0)
    x = x_start.clone()
    for index in range(len(sigmas) - 1):
        sigma = float(sigmas[index])
        timestep = 1.0 - sigma
        model_output = _velocity(_curved_denoised(sigma, x), x, timestep)
        x = zero_eta.step(model_output, timestep, x, sigma, float(sigmas[index + 1]))

    reference = _run(RES_MULTISTEP, sigmas, _curved_denoised, x_start)
    torch.testing.assert_close(x, reference, rtol=0, atol=1e-6)


def test_er_sde_drift_matches_an_independently_derived_analytic_solution_in_expectation():
    """SECOND, independent check of the drift formula. `eta=0` collapses
    `b_eta`'s `/(1+eta)` factor to a division by 1 -- a bug in that factor
    would NOT show up in `test_er_sde_at_eta_zero...` above. This test runs
    at `eta=0.5` instead, against a closed form re-derived independently here
    (substituting `u = x/sigma^(1+eta)` into `dx/dsigma = (1+eta)/sigma*(x-D)`
    for `D = A + B*sigma^2`, the same method `_curved_analytic` uses,
    generalized -- verified offline against `scipy.integrate.solve_ivp` to
    1e-12 before being trusted as a test oracle).

    `_curved_denoised` is x-independent, so injected noise (zero mean, never
    fed back into `D`) leaves the trajectory's MEAN over many seeds on the
    drift-only analytic solution up to Monte Carlo error -- no small-noise
    approximation needed, just enough seeds to average the noise out. A FINE
    (40-step, geometric) grid keeps the discretization error itself
    negligible (~3e-4 here, measured) next to the tolerance below, so the
    tolerance is checking the formula, not step count: a coarse 3-4 step grid
    was tried first and rejected -- ordinary second-order discretization
    error on it (~0.1-0.2, ALSO present with noise off) swamped any
    reasonable tolerance and made the comparison meaningless.
    """
    eta = 0.5
    x_start = torch.tensor([0.2, 0.6, -1.1])
    sigma_start, sigma_end = 0.9, 0.2
    sigmas = torch.exp(torch.linspace(math.log(sigma_start), math.log(sigma_end), 41)).tolist()

    k = 1.0 + eta
    ratio_k = (sigma_end / sigma_start) ** k
    analytic = (
        ratio_k * x_start + _A * (1.0 - ratio_k)
        + (-k * _B / (2.0 - k)) * (sigma_end ** 2 - (sigma_end ** k) * (sigma_start ** (2.0 - k)))
    )

    outputs = []
    for seed in range(300):
        stepper = ERSDEStepper(torch.Generator().manual_seed(seed), eta=eta)
        x = x_start.clone()
        for index in range(len(sigmas) - 1):
            sigma = float(sigmas[index])
            timestep = 1.0 - sigma
            model_output = _velocity(_curved_denoised(sigma, x), x, timestep)
            x = stepper.step(model_output, timestep, x, sigma, float(sigmas[index + 1]))
        outputs.append(x)
    mean = torch.stack(outputs).mean(dim=0)
    torch.testing.assert_close(mean, analytic, rtol=0.0, atol=0.05)


def test_er_sde_is_reproducible_given_the_same_seed_and_varies_with_a_different_one():
    # Deliberately does NOT end at sigma_next=0.0: the terminal step returns
    # `D` directly (every sampler's shared, noise-free contract), and
    # `_curved_denoised` is x-independent by construction, so a schedule
    # ending there would land on the same value for every seed regardless of
    # whether noise injection works at all -- not the property being tested.
    sigmas = [0.9, 0.7, 0.5, 0.3]
    x_start = torch.tensor([0.2, 0.6, -1.1])

    def run(seed):
        return _run(ER_SDE, sigmas, _curved_denoised, x_start, generator=torch.Generator().manual_seed(seed))

    same_a, same_b = run(11), run(11)
    torch.testing.assert_close(same_a, same_b, rtol=0, atol=0)
    assert not torch.allclose(same_a, run(12))


def test_er_sde_step_variance_matches_the_closed_form_noise_std():
    """Independent check of the noise term (not shared code with the
    implementation): repeat ONE non-terminal step from a fixed `(sample,
    sigma, sigma_next)` many times with different seeds and compare the
    sample variance of the results to `noise_std**2` from the module
    docstring's closed form, `noise_std = sigma_next * sqrt(1 -
    (sigma_next/sigma)**(2*eta))`."""
    sigma, sigma_next, eta = 0.6, 0.3, 0.5
    timestep = 1.0 - sigma
    sample = torch.tensor([1.0, -2.0, 0.5])
    velocity = torch.tensor([0.4, -0.6, 0.2])
    expected_std = sigma_next * math.sqrt(1.0 - (sigma_next / sigma) ** (2.0 * eta))

    outputs = torch.stack([
        ERSDEStepper(torch.Generator().manual_seed(seed), eta=eta).step(
            velocity, timestep, sample, sigma, sigma_next,
        )
        for seed in range(400)
    ])
    empirical_std = outputs.std(dim=0, unbiased=True)
    torch.testing.assert_close(empirical_std, torch.full_like(empirical_std, expected_std), rtol=0.25, atol=0.0)


# -- sa_solver: order-2 warmup, the sigma=1.0 trap, the diffusers reference -----

def test_sa_solver_defers_to_order_one_across_h3s_sigma_equals_one_first_knot():
    """The sigma=1.0 TRAP: `res_multistep`/`dpmpp_2m` reach order 2 on the
    SECOND step of every H3 schedule (they only need `prev_denoised is not
    None`); `sa_solver` needs one MORE step here, because its history from
    the sigma=1.0 knot carries a `-inf` lambda -- so its first TWO steps must
    equal `euler_step`, not just its first one."""
    video = build_sigma_schedule(8, VIDEO_SHIFT).sigmas.tolist()
    assert video[0] == 1.0  # the premise this test pins

    stepper = make_stepper(SA_SOLVER)
    x = torch.tensor([0.3, -0.5, 0.2])
    for index in range(2):
        sigma, sigma_next = float(video[index]), float(video[index + 1])
        timestep = 1.0 - sigma
        model_output = _velocity(_curved_denoised(sigma, x), x, timestep)
        expected = euler_step(model_output, timestep, x, sigma, sigma_next)
        x = stepper.step(model_output, timestep, x, sigma, sigma_next)
        torch.testing.assert_close(x, expected, rtol=0, atol=1e-6)


def test_sa_solver_matches_the_diffusers_reference_scheduler():
    """Equivalence test: `SASolverStepper` against diffusers'
    `SASolverScheduler` itself (Apache-2.0), driven by hand past its normal
    `set_timesteps` driver so both walk the IDENTICAL sigma grid -- `tau=0`
    (deterministic), `predictor_order=corrector_order=2`,
    `algorithm_type="data_prediction"`, flow sigmas.

    The grid stays strictly inside `(0, 1)`: `sigma=1.0`/`sigma=0.0` are
    singular/terminal special cases on the H3 side (tested separately above
    and via the terminal-step test) and diffusers' own flow-sigma
    `convert_model_output` isn't exercised there either, so this test is
    only about the shared order-2 predictor/corrector math in the interior.

    `_curved_denoised` is independent of `x` by construction, so feeding
    each implementation the model_output that makes ITS OWN `data_estimate`/
    `convert_model_output` land exactly on the same target `D` decouples the
    comparison from any incidental float32 drift between the two `x`
    trajectories -- a real formula bug would show up as a rapidly growing
    gap, not a rounding-level one.
    """
    from diffusers.schedulers.scheduling_sasolver import SASolverScheduler

    sigmas = [0.9, 0.72, 0.5, 0.31, 0.15]
    x_start = torch.tensor([0.3, -0.4, 0.9])

    ref = SASolverScheduler(
        predictor_order=2, corrector_order=2, algorithm_type="data_prediction",
        prediction_type="flow_prediction", use_flow_sigmas=True, lower_order_final=False,
        tau_func=lambda t: 0.0,
    )
    ref.num_inference_steps = len(sigmas) - 1
    ref.sigmas = torch.tensor(sigmas, dtype=torch.float32)
    ref.model_outputs = [None] * max(ref.config.predictor_order, ref.config.corrector_order - 1)
    ref.timestep_list = [None] * len(ref.model_outputs)
    ref.lower_order_nums = 0
    ref.last_sample = None
    ref._step_index = 0
    ref._begin_index = 0

    ref_x = x_start.clone()
    for index in range(len(sigmas) - 1):
        sigma = sigmas[index]
        target = _curved_denoised(sigma, None)
        model_output = (ref_x - target) / sigma
        ref_x = ref.step(model_output, index, ref_x, return_dict=False)[0]

    got = _run(SA_SOLVER, sigmas, _curved_denoised, x_start)
    torch.testing.assert_close(got, ref_x, rtol=0, atol=1e-5)
