"""Per-stream steppers for MiniMax-H3's dual-clock sampling loop.

A stepper consumes exactly what :func:`schedule.euler_step` consumes -- one
model output, its timestep, the current sample and the two grid sigmas -- and
returns the next sample. `main.py` builds ONE stepper per stream per window,
so the multistep history below is per stream (video and audio advance on
different sigma grids inside one transformer call) and per window (a window
is an independent trajectory, and its first step legitimately has no past).

All three work in x0/"denoised" space via :func:`schedule.data_estimate`, so
H3's reversed-RF convention (`x0 = x_t + sigma*v`, `t = 1 - sigma`) is dealt
with once, at the top of every update, and the solver algebra below is
written purely in `(x, sigma, denoised)`.

The ODE
-------
In `(x, sigma, denoised)` the trajectory obeys

    dx/dsigma = (x - D) / sigma,          D = the model's x0 estimate

which is the same relation Euler's `x_next = ratio*x + (1-ratio)*D`,
`ratio = sigma_next/sigma`, integrates with D held constant. Substituting
`u = x/sigma` removes the linear term exactly:

    du/dsigma = -D / sigma^2

and with `t = -log(sigma)` (so `dsigma = -sigma dt`) that integrates to

    x_next = ratio*x + exp(-t_next) * INT_{t}^{t_next} D(tau) exp(tau) dtau.   (*)

Every sampler here is (*) under a different guess for `D(tau)`.

`euler`
    `D(tau) = D_t`. The integral collapses to `D_t*(1 - ratio)`, i.e. exactly
    :func:`schedule.euler_step` -- which every stepper here CALLS for its
    first-order steps, so neither the default path nor a multistep sampler's
    fallback is a re-derivation of the reference math: it is that math.

`res_multistep`
    Second-order exponential Adams-Bashforth: `D(tau)` is the LINE through
    the current estimate `D_t` and the previous step's `D_prev`, i.e.
    `D(tau) = D_t + (tau - t)*delta` with `delta = (D_t - D_prev)/h_last`,
    `h_last = t - t_prev = log(sigma_prev/sigma)`. With `h = t_next - t =
    log(sigma/sigma_next)`, the two integrals in (*) are elementary --
    `INT exp(tau) dtau` and `INT (tau-t) exp(tau) dtau = exp(t)*((h-1)e^h + 1)`
    -- and give

        x_next = ratio*x + (1 - ratio)*D_t + (h - 1 + ratio)*delta.

    Derived here from (*) rather than ported: ComfyUI's `res_multistep` is
    GPL-3 and nothing from it is used. The phi-function form quoted in the
    RES literature is the same expression regrouped -- with
    `phi1(z) = (e^z - 1)/z` and `phi2(z) = (e^z - 1 - z)/z^2`,
    `h*phi1(-h) = 1 - ratio` and `h*phi2(-h)/r = (h - 1 + ratio)/h_last`
    for `r = h_last/h`, which is the coefficient on `D_t - D_prev` above.

`dpmpp_2m`
    DPM-Solver++(2M), adapted from the MIT-licensed k-diffusion vendored at
    `vendor/k_diffusion/sampling.py::sample_dpmpp_2m` (Copyright (c) 2022
    Katherine Crowson). Same second-order data, different weighting: it
    extrapolates the DATA estimate to the interval midpoint,
    `D_ext = (1 + 1/(2r))*D_t - (1/(2r))*D_prev` with `r = h_last/h`, then
    takes a first-order step on it. k-diffusion's
    `x = (sigma_fn(t_next)/sigma_fn(t))*x - (-h).expm1()*D_ext` IS
    `ratio*x + (1 - ratio)*D_ext` once `sigma_fn`/`t_fn` are substituted,
    which is why the update reads as an ordinary blend here.

    The two second-order weights agree to leading order (both `~h^2/2 *
    (D_t - D_prev)/h_last`); they differ in how much of the exponential they
    keep, which is the whole practical difference between the two samplers.

`er_sde`
    Stochastic. "Elucidating the solution space of extended reverse-time SDE
    for diffusion models" (Cui et al., arXiv:2309.06169, WACV 2025) extends
    the ODE above with an independent noise-injection rate `h(sigma) =
    sqrt(eta) * g(sigma)`, `eta >= 0` a "how much SDE" dial (the paper states
    `eta=0` is exactly the ODE and `eta=1` the standard reverse SDE, for its
    `phi(sigma)=sigma` / `phi(sigma)=sigma^2` special cases). For this
    module's VE-type sigma (`x_sigma = D + sigma*eps`, forward rate
    `g(sigma)^2 = 2*sigma`), Tweedie's formula gives the score
    `-(x-D)/sigma^2`; substituting into the reverse SDE and applying the SAME
    `u = x/sigma^(1+eta)` integrating factor the ODE derivation above uses
    (its `eta=0` case) gives a MEAN that is the ODE update with `ratio`
    replaced everywhere by `ratio_eta = (sigma_next/sigma)^(1+eta)` -- at
    `eta=0` this collapses to bit-for-bit the `res_multistep` correction
    weight `b = h - (1-ratio)` above, the check this derivation was verified
    against -- plus an independent, `D`-free VARIANCE from integrating
    `sqrt(2*eta*sigma)` through the same substitution:
    `noise_std = sigma_next * sqrt(1 - (sigma_next/sigma)^(2*eta))`.
    `eta = 0.5` (`phi(sigma) = sigma^1.5`, the paper's own simplest named
    design, "ER SDE 1") is fixed internally -- no preset knob.

    Re-derived here, NOT ported from the paper's own MIT-licensed reference
    (github.com/QinpengCui/ER-SDE-Solver): that code targets pixel-space VE
    diffusion with a `phi(sigma)` family tuned for ~100-NFE image sampling,
    which does not carry over to this module's x0-space, few-step,
    reversed-RF ODE -- the same posture `res_multistep` takes for a GPL
    source, applied here because the parameterizations differ rather than
    because of the license.

`sa_solver`
    Predictor-corrector, order <= 2, deterministic (`tau=0` --
    "SA-Solver will sample from vanilla diffusion ODE if tau_func is set to
    lambda t: 0", diffusers' own docstring). Ported from diffusers'
    `SASolverScheduler.stochastic_adams_bashforth_update`/
    `stochastic_adams_moulton_update` (Apache-2.0,
    `diffusers/schedulers/scheduling_sasolver.py`, "Copyright 2025 Shuchen Xue
    ... and The HuggingFace Team"), restricted to its `algorithm_type=
    "data_prediction"` branch and re-expressed against this module's shared
    `data_estimate`/`(x, sigma, denoised)` contract instead of the
    reference's stateful scheduler object. `tau=0` rather than the
    reference's guided-sampling default (nonzero only for discrete timesteps
    in [200, 800] of a 1000-step DDPM grid, which has no correspondent on
    this continuous flow schedule): `er_sde` above already covers this pipe's
    stochastic option, and a deterministic solver is the one whose output is
    checkable bit-for-bit against the reference (see the equivalence test in
    `tests/pipelines/pipes/generator/video_minimax_h3/test_samplers.py`).
    Orders default to the reference's own `predictor_order=corrector_order=
    2`. At order 1 (no history) both predictor and corrector collapse to the
    plain first-order exponential update -- proved algebraically, not just
    asserted -- so the first step delegates to `euler_step` like
    `res_multistep`/`dpmpp_2m` do.

    **H3's sigma=1.0 trap.** Every H3 schedule's FIRST knot is exactly
    `sigma=1.0` (both streams, every shift -- `schedule.py`'s
    `build_sigma_schedule` docstring), and SA-Solver's own time variable
    `lambda(sigma) = log((1-sigma)/sigma)` (`alpha_t = 1-sigma`, `sigma_t =
    sigma` -- diffusers' flow-sigma branch) is `-inf` there. That is a
    property of the algorithm on this grid, not a porting bug: the
    unmodified reference scheduler fed the same schedule hits the same
    singularity. History carrying a non-finite lambda is therefore treated
    as unusable (order stays at 1 one step longer) rather than fed into the
    order-2 formulas -- the same one-step-late treatment
    `_MultistepStepper` already gives genuinely missing history.

Terminal step
-------------
`sigma_next == 0` sends `h` to infinity, so every second-order update falls
back to the first-order one there (`ratio = 0` makes it land exactly on
`D_t`) -- the standard guard, and the same one k-diffusion applies. `er_sde`'s
noise term vanishes at the same point (`noise_std = 0 * sqrt(1 - 0) = 0`), so
the terminal step is noise-free like every other sampler's.
"""

from __future__ import annotations

import math
from typing import Optional

import torch

from .schedule import data_estimate, euler_step

Tensor = torch.Tensor

EULER = "euler"
RES_MULTISTEP = "res_multistep"
DPMPP_2M = "dpmpp_2m"
ER_SDE = "er_sde"
SA_SOLVER = "sa_solver"
SAMPLERS = (EULER, RES_MULTISTEP, DPMPP_2M, ER_SDE, SA_SOLVER)

# ER-SDE's noise-extension exponent, fixed at the paper's own simplest named
# design ("ER SDE 1: phi(sigma) = sigma^1.5", i.e. eta=0.5 in the
# phi(sigma)=sigma^(1+eta) family the module docstring derives) -- kept
# internal rather than exposed as a preset knob.
_ER_SDE_ETA = 0.5


def _compute_dtype(sample: Tensor) -> torch.dtype:
    return torch.float32 if sample.dtype in (torch.float16, torch.bfloat16) else sample.dtype


class EulerStepper:
    """First-order. Delegates to :func:`schedule.euler_step` verbatim so that
    the default sampler cannot drift from the reference implementation."""

    def step(
        self, model_output: Tensor, timestep: float | Tensor, sample: Tensor,
        sigma: float | Tensor, sigma_next: float | Tensor,
    ) -> Tensor:
        return euler_step(model_output, timestep, sample, sigma, sigma_next)


class _MultistepStepper:
    """Shared plumbing for the two second-order samplers: the x0 estimate, the
    float32 compute dtype, the first-step/terminal-step fallback to Euler, and
    the one-slot history.

    A model output that the step cache REPLAYED enters the history unchanged.
    It is the model's most recent real velocity, and the sample it is applied
    to did move, so the pair is consistent; fabricating a substitute estimate
    would put a number the model never produced into the extrapolation.
    """

    def __init__(self) -> None:
        self._prev_denoised: Optional[Tensor] = None
        self._prev_sigma: Optional[float] = None

    def step(
        self, model_output: Tensor, timestep: float | Tensor, sample: Tensor,
        sigma: float | Tensor, sigma_next: float | Tensor,
    ) -> Tensor:
        dtype = _compute_dtype(sample)
        denoised = data_estimate(model_output, timestep, sample).to(dtype)
        sigma_now = float(sigma)
        sigma_then = float(sigma_next)

        first_order = (
            self._prev_denoised is None
            or sigma_then == 0.0
            or self._prev_sigma is None
            or self._prev_sigma <= sigma_now
        )
        if first_order:
            # Delegated, not re-derived: computing the same blend from a
            # Python-float ratio double-rounds it and drifts ~1 ULP off the
            # euler path these samplers must reduce to on their first step.
            prev_sample = euler_step(model_output, timestep, sample, sigma, sigma_next)
        else:
            h = math.log(sigma_now / sigma_then)
            h_last = math.log(self._prev_sigma / sigma_now)
            prev_sample = self._second_order(
                sample.to(dtype), denoised, self._prev_denoised.to(dtype),
                ratio=sigma_then / sigma_now, h=h, h_last=h_last,
            ).to(dtype=sample.dtype)

        self._prev_denoised = denoised
        self._prev_sigma = sigma_now
        return prev_sample

    def _second_order(
        self, x: Tensor, denoised: Tensor, prev_denoised: Tensor, *, ratio: float, h: float, h_last: float,
    ) -> Tensor:
        raise NotImplementedError


class ResMultistepStepper(_MultistepStepper):
    """Second-order exponential Adams-Bashforth -- see the module docstring's
    `res_multistep` derivation."""

    def _second_order(self, x, denoised, prev_denoised, *, ratio, h, h_last):
        delta = (denoised - prev_denoised) / h_last
        return ratio * x + (1.0 - ratio) * denoised + (h - 1.0 + ratio) * delta


class DPMPlusPlus2MStepper(_MultistepStepper):
    """DPM-Solver++(2M) -- see the module docstring's `dpmpp_2m` note for the
    k-diffusion (MIT) update rule this adapts."""

    def _second_order(self, x, denoised, prev_denoised, *, ratio, h, h_last):
        r = h_last / h
        extrapolated = (1.0 + 1.0 / (2.0 * r)) * denoised - (1.0 / (2.0 * r)) * prev_denoised
        return ratio * x + (1.0 - ratio) * extrapolated


class ERSDEStepper:
    """Stochastic second-order ER-SDE stepper -- see the module docstring's
    `er_sde` derivation. Needs a seeded `torch.Generator` (the same one the
    caller draws the window's video/audio noise from) to draw one noise
    tensor per non-terminal step."""

    def __init__(self, generator: torch.Generator, *, eta: float = _ER_SDE_ETA) -> None:
        self._generator = generator
        self._eta = eta
        self._prev_denoised: Optional[Tensor] = None
        self._prev_sigma: Optional[float] = None

    def step(
        self, model_output: Tensor, timestep: float | Tensor, sample: Tensor,
        sigma: float | Tensor, sigma_next: float | Tensor,
    ) -> Tensor:
        dtype = _compute_dtype(sample)
        denoised = data_estimate(model_output, timestep, sample).to(dtype)
        sigma_now = float(sigma)
        sigma_then = float(sigma_next)
        eta = self._eta

        if sigma_then == 0.0:
            prev_sample = denoised.to(dtype=sample.dtype)
        else:
            ratio_eta = (sigma_then / sigma_now) ** (1.0 + eta)
            x = sample.to(dtype)
            # NOT `euler_step`: `ratio_eta != sigma_then/sigma_now` for
            # eta > 0, so this sampler's own first-order form is genuinely
            # different math, not a re-derivation the shared helper would
            # keep in sync with.
            if self._prev_denoised is None or self._prev_sigma is None or self._prev_sigma <= sigma_now:
                mean = ratio_eta * x + (1.0 - ratio_eta) * denoised
            else:
                h = math.log(sigma_now / sigma_then)
                h_last = math.log(self._prev_sigma / sigma_now)
                delta = (denoised - self._prev_denoised.to(dtype)) / h_last
                b_eta = h - (1.0 - ratio_eta) / (1.0 + eta)
                mean = ratio_eta * x + (1.0 - ratio_eta) * denoised + b_eta * delta
            noise_var = max(0.0, 1.0 - (sigma_then / sigma_now) ** (2.0 * eta))
            noise_std = sigma_then * math.sqrt(noise_var)
            noise = torch.randn(sample.shape, generator=self._generator, device=sample.device, dtype=dtype)
            prev_sample = (mean + noise_std * noise).to(dtype=sample.dtype)

        self._prev_denoised = denoised
        self._prev_sigma = sigma_now
        return prev_sample


def _flow_lambda(sigma: float) -> float:
    """`log(alpha_t/sigma_t)` for H3's flow sigmas (`alpha_t = 1-sigma`,
    `sigma_t = sigma`) -- SA-Solver's own time variable, in which its
    Adams-Bashforth/Moulton updates are exact exponential integrals (the same
    `phi`-function idiom `res_multistep` uses in `t = -log(sigma)`, a
    different but equally standard change of variable). `+inf` at `sigma=0`,
    `-inf` at `sigma=1` -- both handled by `SASolverStepper` rather than
    reaching this function's callers at those values. `math.log(0)` RAISES
    (unlike numpy, which returns `-inf`), so `sigma=1.0` is special-cased
    here rather than left to hit that exception."""
    if sigma >= 1.0:
        return -math.inf
    return math.log((1.0 - sigma) / sigma)


class SASolverStepper:
    """Deterministic (`tau=0`) order-<=2 predictor-corrector -- see the
    module docstring's `sa_solver` derivation for the diffusers source, the
    order-1-collapses-to-`euler_step` proof and the `sigma=1.0` trap."""

    def __init__(self) -> None:
        self._prev_denoised: Optional[Tensor] = None
        self._prev_lambda: Optional[float] = None
        self._prev_sigma: Optional[float] = None
        self._last_sample: Optional[Tensor] = None

    def step(
        self, model_output: Tensor, timestep: float | Tensor, sample: Tensor,
        sigma: float | Tensor, sigma_next: float | Tensor,
    ) -> Tensor:
        dtype = _compute_dtype(sample)
        denoised = data_estimate(model_output, timestep, sample).to(dtype)
        sigma_now = float(sigma)
        sigma_then = float(sigma_next)
        lam_now = _flow_lambda(sigma_now)

        history_ready = (
            self._prev_denoised is not None and self._prev_lambda is not None
            and math.isfinite(self._prev_lambda)
        )

        x = sample.to(dtype)
        if history_ready:
            x = self._correct(
                denoised, lam_now=lam_now, sigma_now=sigma_now,
                prev_denoised=self._prev_denoised.to(dtype), lam_prev=self._prev_lambda,
                sigma_prev=self._prev_sigma, last_sample=self._last_sample.to(dtype),
            )
        self._last_sample = x

        if sigma_then == 0.0:
            prev_sample = denoised.to(dtype=sample.dtype)
        elif not history_ready:
            prev_sample = euler_step(model_output, timestep, sample, sigma, sigma_next)
        else:
            lam_next = _flow_lambda(sigma_then)
            prev_sample = self._predict(
                x, denoised, sigma_now=sigma_now, sigma_next=sigma_then, lam_now=lam_now, lam_next=lam_next,
                prev_denoised=self._prev_denoised.to(dtype), lam_prev=self._prev_lambda,
            ).to(dtype=sample.dtype)

        self._prev_denoised = denoised
        self._prev_lambda = lam_now
        self._prev_sigma = sigma_now
        return prev_sample

    @staticmethod
    def _exp_integral(order: int, start: float, end: float) -> float:
        """`integral_start^end e^x * x^order dx`, order in {0, 1} -- the only
        two `SASolverScheduler.get_coefficients_exponential_positive` orders
        an order-2 (predictor_order=corrector_order=2) solver ever needs."""
        if order == 0:
            return math.exp(end) - math.exp(start)
        return (end - 1.0) * math.exp(end) - (start - 1.0) * math.exp(start)

    @staticmethod
    def _lagrange_weights(i0: float, i1: float, lam_now: float, lam_prev: float) -> tuple[float, float]:
        """`lagrange_polynomial_coefficient(1, [lam_now, lam_prev])` folded
        against the exponential-integral moments `(i0, i1)` -- shared by the
        predictor and the corrector, which build the SAME Lagrange nodes
        `[lam_now, lam_prev]` and differ only in which interval `(i0, i1)`
        were integrated over."""
        gap = lam_now - lam_prev
        c_now = (i1 - lam_prev * i0) / gap
        c_prev = -(i1 - lam_now * i0) / gap
        return c_now, c_prev

    def _predict(self, x, denoised, *, sigma_now, sigma_next, lam_now, lam_next, prev_denoised, lam_prev):
        i0 = self._exp_integral(0, lam_now, lam_next)
        i1 = self._exp_integral(1, lam_now, lam_next)
        c_now, c_prev = self._lagrange_weights(i0, i1, lam_now, lam_prev)
        h = lam_next - lam_now
        # order==2 correction, "similar to UniPC" in
        # SASolverScheduler.stochastic_adams_bashforth_update's own
        # unconditional order==2 branch.
        extra = math.exp(lam_next) * (h * h / 2.0 - (h - 1.0 + math.exp(-h))) / (lam_now - lam_prev)
        c_now, c_prev = c_now + extra, c_prev - extra
        gradient = sigma_next * (c_now * denoised + c_prev * prev_denoised)
        return (sigma_next / sigma_now) * x + gradient

    def _correct(self, denoised, *, lam_now, sigma_now, prev_denoised, lam_prev, sigma_prev, last_sample):
        i0 = self._exp_integral(0, lam_prev, lam_now)
        i1 = self._exp_integral(1, lam_prev, lam_now)
        c_now, c_prev = self._lagrange_weights(i0, i1, lam_now, lam_prev)
        h = lam_now - lam_prev
        # order==2 correction, the corrector's own counterpart of the
        # predictor's tweak above (SASolverScheduler.
        # stochastic_adams_moulton_update's unconditional order==2 branch).
        extra = math.exp(lam_now) * (h / 2.0 - (h - 1.0 + math.exp(-h)) / h)
        c_now, c_prev = c_now + extra, c_prev - extra
        gradient = sigma_now * (c_now * denoised + c_prev * prev_denoised)
        return (sigma_now / sigma_prev) * last_sample + gradient


_STEPPERS = {
    EULER: EulerStepper,
    RES_MULTISTEP: ResMultistepStepper,
    DPMPP_2M: DPMPlusPlus2MStepper,
    SA_SOLVER: SASolverStepper,
}


def make_stepper(sampler: str, *, generator: Optional[torch.Generator] = None):
    """A FRESH stepper -- one per stream per window; the multistep history is
    the stepper's own state and must not outlive either boundary.

    `generator`: required for `er_sde` (it draws one noise tensor per
    non-terminal step); ignored by every deterministic sampler."""
    if sampler == ER_SDE:
        if generator is None:
            raise ValueError(f"sampler {ER_SDE!r} needs a seeded torch.Generator -- it draws noise every step")
        return ERSDEStepper(generator)
    try:
        return _STEPPERS[sampler]()
    except KeyError:
        raise ValueError(f"sampler must be one of {SAMPLERS}, got {sampler!r}") from None
