"""Euler flow-matching sampler step.

For a flow-matching (CONST-prediction) model the network predicts a velocity
``v`` and the Euler update is exact per step:

    v      = guidance(model_fn, x, sigma_i, cond, uncond, i)   # predicted velocity
    x0_est = x - sigma_i * v                                    # clean-latent estimate
    x      = x + (sigma_{i+1} - sigma_i) * v                    # euler step

This matches ComfyUI's ``sample_euler`` specialised to CONST: there
``denoised = x - v*sigma`` and ``d = to_d(x, sigma, denoised) = v``, so
``x += d * (sigma_next - sigma)`` is identical to the update above. The
``x0_est`` is handed to hooks (preview decode / progress) each step.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


@torch.no_grad()
def sample_euler(
    model_fn,
    x: Tensor,
    sigmas: Tensor,
    guidance: GuidanceStrategy,
    cond: dict,
    uncond: dict | None = None,
    hooks=(),
    is_cancelled=None,
    sampler_options: dict | None = None,
    start_step: int = 0,
) -> Tensor:
    """Run the Euler loop over ``sigmas`` and return the final latent.

    ``model_fn(x, sigma, conditioning) -> velocity``. ``sigmas`` is descending,
    length ``steps + 1`` with a final ``0.0``. ``guidance`` combines the forward
    pass(es) into one velocity. Hooks fire once per step (after the step, with
    the x0 estimate). ``is_cancelled()`` is polled each step and raises
    :class:`SamplingCancelled` cleanly when it returns truthy.

    ``sigma`` is passed to ``model_fn`` as a 1-D tensor broadcast over the batch
    (shape ``(batch,)``), matching how flow models expect a per-sample timestep.

    ``sampler_options`` is part of the uniform :data:`~..denoise_loop.SAMPLERS`
    contract (see ``sampling/algorithms/euler_sde.py`` for a sampler that
    actually reads it); the deterministic Euler step has no options and ignores
    it.

    ``start_step`` (trajectory warm-start; see
    :mod:`~src.platform.runtime.native.sampling.trajectory_cache`) skips the loop's first
    ``start_step`` iterations — the caller has already supplied ``x`` as the
    on-trajectory state entering step ``start_step`` and passes the FULL sigma
    array, so global step indices (hence per-step guidance) are preserved and the
    tail is bit-identical to the cold run's. ``0`` (default) is the normal path.
    """
    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    s_in = x.new_ones((x.shape[0],))
    try:
        for i in range(start_step, total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0_est = x - sigma * v
            x = x + (sigma_next - sigma) * v

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0_est)
    finally:
        run_hooks(hooks, "on_end")

    return x
