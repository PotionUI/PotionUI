"""ER-SDE-Solver-3 for flow-matching (CONST-prediction) models.

Reference: *Elucidating the solution space of extended reverse-time SDE for
diffusion models*, Cui et al., arXiv:2309.06169, and the authors' reference
implementation at https://github.com/QinpengCui/ER-SDE-Solver (MIT License,
Copyright (c) 2023 Qinpeng Cui). Derived from the paper and that MIT source --
NOT transcribed from ComfyUI (GPL). ComfyUI was consulted only to pin the
user-visible DEFAULTS, so a preset tuned on Civitai lands in the same place
here: the VP (``lambda = sigma/alpha``) form, ``max_stage=3``, the noise scaler
``phi(lambda) = lambda * (exp(lambda**0.3) + 10)``, 200 integration points and
``s_noise=1.0``.

What the solver is
------------------
ER-SDE adds an *extra* reverse-time diffusion term whose strength is a free
function of the noise level. That freedom is the ``phi`` ("noise scaler")
below: the whole family shares one update whose only per-variant quantity is
the ratio ``r = phi(lambda_t) / phi(lambda_s)``. ``phi(lambda) = lambda``
recovers the probability-flow ODE exactly (the diffusion coefficient vanishes
identically, see below); the default ``phi`` grows super-linearly, so early
steps replace more of the state with fresh noise than an ODE would -- the
source of the "more realistic texture" people tune it for.

Flow-matching (VP) adaptation
-----------------------------
The reference implementation is written for VE diffusion, where the state is
``x = x0 + sigma*eps``. A flow/CONST model instead holds
``x = alpha_t*x0 + sigma_t*eps`` with ``alpha_t = 1 - sigma_t``, and the model
returns a velocity ``v`` from which ``x0 = x - sigma*v``. Dividing the VP state
by ``alpha`` turns it into a VE state,

    x/alpha = x0 + lambda*eps ,      lambda = sigma/alpha ,

so the VE solver applies verbatim in that rescaled space with ``lambda`` in
place of ``sigma``. Multiplying the VE update back by ``alpha_t`` gives the
form used here (``s`` = current level, ``t`` = next):

    r_alpha = alpha_t / alpha_s
    r       = phi(lambda_t) / phi(lambda_s)
    x  = r_alpha*r * x + alpha_t*(1 - r) * x0                       (stage 1)
    x += alpha_t * (dt + s_int*phi(lambda_t)) * dx0                 (stage 2)
    x += alpha_t * (dt**2/2 + s_u*phi(lambda_t)) * ddx0             (stage 3)
    x += eps_fresh * sqrt(sigma_t**2 - sigma_s**2 * r_alpha**2 * r**2) * s_noise

with ``dt = lambda_t - lambda_s``, ``dx0``/``ddx0`` the first/second finite
differences of the ``x0`` estimates over ``lambda``, and ``s_int``/``s_u`` the
integrals ``∫ 1/phi`` and ``∫ (u - lambda_s)/phi`` over ``[lambda_t,
lambda_s]``, evaluated as the reference does with a fixed-count Riemann sum.
The diffusion coefficient is the VE one (``lambda_t**2 - lambda_s**2 * r**2``)
scaled by ``alpha_t**2``, which is exactly the ``sigma``-space expression above.

Substituting ``phi(lambda) = lambda`` collapses stage 1 to
``r_alpha*r == sigma_t/sigma_s`` and ``alpha_t*(1 - r) == 1 - sigma_t/sigma_s``
(the ``alpha_s`` factors cancel), i.e. exactly :func:`~.euler.sample_euler`,
and drives the radicand to zero -- the module test asserts both.

``sigma == 1`` (the first step of a txt2img schedule)
-----------------------------------------------------
``alpha_s`` is then ``0`` and ``lambda_s`` infinite, so ``r_alpha`` and
``lambda_s`` both blow up and their product is indeterminate. ``alpha`` is
therefore floored at :data:`_ALPHA_FLOOR` (an SNR floor: it caps ``lambda`` at
``1/_ALPHA_FLOOR``) for the ``lambda``/``r_alpha`` computation ONLY -- the
diffusion term and ``x0 = x - sigma*v`` keep the true ``sigma``. Flooring
``alpha`` rather than clamping ``sigma`` keeps ``r_alpha*r`` algebraically
exact under an identity scaler; only the ``alpha_t*(1 - r)`` term carries an
``O(_ALPHA_FLOOR)`` deviation, and only on a step that starts at ``sigma == 1``
where ``x`` is pure noise carrying no information about ``x0`` anyway.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor

_ALPHA_FLOOR = 1e-4
_INTEGRATION_POINTS = 200


def _fresh_noise(x: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        return torch.randn_like(x)
    return torch.randn(x.shape, generator=generator, device=x.device, dtype=x.dtype)


def _default_scaler(lam: Tensor) -> Tensor:
    return lam * (lam.pow(0.3).exp() + 10.0)


def _identity_scaler(lam: Tensor) -> Tensor:
    return lam


_SCALERS = {"default": _default_scaler, "identity": _identity_scaler}


@torch.no_grad()
def sample_er_sde(
    model_fn,
    x: Tensor,
    sigmas: Tensor,
    guidance: GuidanceStrategy,
    cond: dict,
    uncond: dict | None = None,
    hooks=(),
    is_cancelled=None,
    sampler_options: dict | None = None,
) -> Tensor:
    """ER-SDE-Solver-3 loop. Same signature/semantics as :func:`sample_euler`.

    ``sampler_options``:

    * ``max_stage`` (default ``3``) — solver order, ``1``/``2``/``3``. A step
      uses ``min(max_stage, step_index + 1)``, so the first steps warm up at a
      lower order until enough ``x0`` history exists.
    * ``s_noise`` (default ``1.0``) — scale on the injected noise. ``0.0``
      makes the run deterministic (but NOT equal to an ODE solver: the drift
      still follows the SDE's ``phi``).
    * ``noise_scaler`` (default ``"default"``) — ``"default"`` for the paper's
      recommended ``phi``, ``"identity"`` for ``phi(lambda) = lambda``, which
      degenerates the solver to the probability-flow ODE (diagnostic).
    * ``generator`` (default ``None``) — ``torch.Generator`` for reproducible
      fresh noise; omitted, falls back to the global RNG.
    """
    opts = sampler_options or {}
    max_stage = int(opts.get("max_stage", 3))
    if max_stage not in (1, 2, 3):
        raise ValueError(f"max_stage must be 1, 2 or 3, got {max_stage}")
    s_noise = float(opts.get("s_noise", 1.0))
    scaler_name = opts.get("noise_scaler", "default")
    if scaler_name not in _SCALERS:
        raise ValueError(f"unknown noise_scaler: {scaler_name!r}")
    scaler = _SCALERS[scaler_name]
    generator = opts.get("generator")

    # Every scalar coefficient is derived in float64: the identity-scaler case
    # cancels the radicand to zero exactly, and a float32 rounding residual of
    # ~1e-8 there comes back out of sqrt() as a ~1e-4 noise amplitude.
    sigmas_hp = sigmas.to(torch.float64)
    alphas = (1.0 - sigmas_hp).clamp(min=_ALPHA_FLOOR)
    lambdas = sigmas_hp / alphas
    points = torch.arange(_INTEGRATION_POINTS, dtype=torch.float64, device=sigmas.device)

    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    s_in = x.new_ones((x.shape[0],))
    old_x0 = None
    old_dx0 = None
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0 = x - sigma * v

            if sigma_next == 0:
                x = x0
            else:
                alpha_next = alphas[i + 1]
                lam = lambdas[i]
                lam_next = lambdas[i + 1]
                phi_next = scaler(lam_next)
                r = phi_next / scaler(lam)
                r_alpha = alpha_next / alphas[i]

                x = r_alpha * r * x + alpha_next * (1.0 - r) * x0

                stage = min(max_stage, i + 1)
                if stage >= 2:
                    dt = lam_next - lam
                    step_size = -dt / _INTEGRATION_POINTS
                    pos = lam_next + points * step_size
                    scaled_pos = scaler(pos)
                    s_int = (1.0 / scaled_pos).sum() * step_size
                    dx0 = (x0 - old_x0) / (lam - lambdas[i - 1])
                    x = x + alpha_next * (dt + s_int * phi_next) * dx0
                    if stage >= 3:
                        s_u = ((pos - lam) / scaled_pos).sum() * step_size
                        ddx0 = (dx0 - old_dx0) / (0.5 * (lam - lambdas[i - 2]))
                        x = x + alpha_next * (0.5 * dt * dt + s_u * phi_next) * ddx0
                    old_dx0 = dx0

                if s_noise != 0.0:
                    var = (
                        sigmas_hp[i + 1] ** 2 - sigmas_hp[i] ** 2 * r_alpha**2 * r**2
                    ).clamp(min=0.0)
                    std = var.sqrt().nan_to_num(nan=0.0)
                    x = x + _fresh_noise(x, generator) * std * s_noise

            old_x0 = x0
            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0)
    finally:
        run_hooks(hooks, "on_end")

    return x
