"""Stochastic (ancestral) Euler step for flow-matching models.

The deterministic :func:`~.euler.sample_euler` step can be re-derived as: hold
the flow interpolation ``x = (1-sigma)*x0 + sigma*eps`` and re-form ``x`` at
``sigma_next`` using the SAME implied noise ``eps`` that produced the current
``x`` from ``x0``. Given ``x0 = x - sigma*v`` and the interpolation, that
implied noise is

    eps = x0 + v = (x - sigma*v) + v = x + (1 - sigma)*v

(no division by ``sigma`` needed, so this stays well-defined at ``sigma -> 0``).
Substituting into ``x_next = (1-sigma_next)*x0 + sigma_next*eps`` reproduces the
plain Euler update exactly (see the module test for the algebra).

The stochastic variant replaces a fraction ``eta`` of that implied noise with
FRESH noise each step, keeping the interpolation variance-preserving:

    eps_mix = sqrt(1 - eta**2) * eps + eta * eps_fresh
    x_next  = (1 - sigma_next) * x0 + sigma_next * eps_mix

``eta == 0`` degenerates to ``eps_mix == eps``, i.e. exactly the deterministic
Euler step (handled as its own branch below to avoid the extra sqrt/randn work
and float noise). ``eta == 1`` is fully ancestral (the kept fraction is zero).
The terminal step (``sigma_next == 0``) is always deterministic — there is
nothing left to inject noise into.
"""

from __future__ import annotations

import math

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


def _fresh_noise(x: Tensor, generator: torch.Generator | None) -> Tensor:
    if generator is None:
        return torch.randn_like(x)
    return torch.randn(x.shape, generator=generator, device=x.device, dtype=x.dtype)


@torch.no_grad()
def sample_euler_sde(
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
    """Ancestral Euler loop. Same signature/semantics as :func:`sample_euler`.

    ``sampler_options``:

    * ``eta`` (default ``1.0``) — fraction of the implied per-step noise
      replaced with fresh noise, in ``[0, 1]``. ``0.0`` reduces exactly to
      :func:`sample_euler`; ``1.0`` is fully ancestral.
    * ``generator`` (default ``None``) — a ``torch.Generator`` used to draw the
      fresh noise reproducibly (matching the caller's seeded generator gives a
      deterministic run); omitted, fresh noise falls back to the global RNG.
    """
    opts = sampler_options or {}
    eta = float(opts.get("eta", 1.0))
    if not (0.0 <= eta <= 1.0):
        raise ValueError(f"eta must be in [0, 1], got {eta}")
    generator = opts.get("generator")

    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    s_in = x.new_ones((x.shape[0],))
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0_est = x - sigma * v

            if eta == 0.0 or sigma_next == 0:
                # Deterministic branch: identical to sample_euler, and the
                # correct terminal behaviour (no noise left to inject at 0).
                x = x + (sigma_next - sigma) * v
            else:
                eps = x + (1.0 - sigma) * v  # implied noise, no division by sigma
                eps_fresh = _fresh_noise(x, generator)
                kept = math.sqrt(max(1.0 - eta * eta, 0.0))
                eps_mix = kept * eps + eta * eps_fresh
                x = (1.0 - sigma_next) * x0_est + sigma_next * eps_mix

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0_est)
    finally:
        run_hooks(hooks, "on_end")

    return x
