"""Deterministic (non-ancestral) Euler with the CFG++ velocity/target split.

CFG++ (Chung et al., "CFG++: Manifold-Constrained Classifier Free Guidance
for Diffusion Models", arXiv:2406.08070) keeps the CFG-guided prediction as
the step's TARGET, but derives the step's DIRECTION from the plain
UNCONDITIONAL prediction instead of the guided one -- see
``euler_ancestral_cfg_pp.py``'s module docstring for the full re-derivation
of the eps/x0 formulation this reuses (identical algebra, ``eta`` fixed at
``0.0`` here instead of user-configurable).

Provenance: this file is a straight specialization of THIS codebase's own
``euler_ancestral_cfg_pp.py`` (already an in-repo re-derivation from the CFG++
paper, not a port of any ComfyUI/GPL sampler -- see that module's docstring
for the citation and the ComfyUI-consultation disclosure) at ``eta=0.0``: no
fresh-noise draw ever happens, so there is no ancestral/stochastic component
and no ``generator``/``eta`` knob to configure. Kept as a separate module
(rather than a thin call into the ancestral one with ``sampler_options={"eta":
0.0}`` baked in) so it appears as its own entry in ``SAMPLERS`` --
distinguishing "the deterministic recipe" from "the ancestral recipe with eta
forced to 0" matters for callers picking a sampler by name (e.g. the LTX-2.3
distilled speed profile, whose first-party recipe is deterministic -- see
``docs/models/ltx.md``).

Target/direction split, per step (``sigma`` -> ``sigma_next``):

* ``v_cfg`` (the guidance strategy's combined output) gives the TARGET:
  ``x0_est = x - sigma * v_cfg``.
* ``v_uncond`` (``guidance.last_uncond_v``, the strategy's raw uncond-branch
  prediction) gives the DIRECTION: ``eps = x + (1 - sigma) * v_uncond``.
* Next state: ``x_next = (1 - sigma_next) * x0_est + sigma_next * eps`` --
  the same DDIM-style anchor ``euler_ancestral_cfg_pp`` uses, which collapses
  EXACTLY to ``x0_est`` at the terminal step (``sigma_next == 0``) regardless
  of any ``v_cfg``/``v_uncond`` mismatch.

Guidance strategies with no uncond branch (``EmbeddedGuidance``, ``NoCFG``, or
``TrueCFG`` at ``scale == 1.0`` -- exactly the LTX-2.3 distilled recipe's own
``cfg=1.0``) expose no ``last_uncond_v`` (or expose ``None``); this sampler
then falls back to ``v_uncond = v_cfg``, at which point the CFG++ split is a
pure no-op and the per-step update reduces algebraically to plain Euler
(``x = x + (sigma_next - sigma) * v_cfg``) -- taken here as an exact shortcut,
not merely a numerically-close approximation, so this sampler is BIT-IDENTICAL
to :func:`~.euler.sample_euler` whenever the guidance strategy has no genuine
uncond branch (in particular, always at ``cfg=1.0``). This is the property the
LTX-2.3 distilled-speed-profile fix relies on: swapping the profile's sampler
from ``euler_ancestral_cfg_pp`` to this one changes nothing about the
per-step math at the recipe's own ``cfg=1.0`` (no ancestral noise was ever
correct there either -- see this file's sibling module for the same finding),
while giving CFG++'s target/direction split for free to any custom profile
that raises CFG above 1.0.
"""

from __future__ import annotations

import torch

from ..cfg import GuidanceStrategy
from ..hooks import run_hooks
from ...errors import SamplingCancelled

Tensor = torch.Tensor


@torch.no_grad()
def sample_euler_cfg_pp(
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
    """Deterministic Euler, CFG++ target/direction decoupling. See module
    docstring for the formula and the ``cfg=1.0`` <-> plain-Euler equivalence.

    Same signature as :func:`~.euler.sample_euler`; ``sampler_options`` is
    part of the uniform :data:`~..denoise_loop.SAMPLERS` contract and is
    accepted but ignored -- there is no per-step randomness to seed and no
    other knob this sampler reads.
    """
    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    # Mirrors euler_ancestral_cfg_pp's own S9 guard: during a TrueCFG
    # zero-init window the guidance call returns a deliberate zero velocity
    # without running an uncond forward this step, so `last_uncond_v` would
    # still hold a STALE value from a prior step. Route those steps around
    # the CFG++ split entirely (v_cfg IS v_uncond IS 0, so this is a pure
    # no-op vs. treating it as "no split available").
    zero_init_steps = getattr(guidance, "zero_init_steps", 0)

    s_in = x.new_ones((x.shape[0],))
    try:
        for i in range(total_steps):
            if is_cancelled is not None and is_cancelled():
                raise SamplingCancelled(step_index=i)

            sigma = sigmas[i]
            sigma_next = sigmas[i + 1]

            v_cfg = guidance(model_fn, x, sigma * s_in, cond, uncond, i)
            x0_est = x - sigma * v_cfg

            no_split = True
            if i < zero_init_steps:
                v_dir = v_cfg
            else:
                v_dir = getattr(guidance, "last_uncond_v", None)
                if v_dir is None:
                    v_dir = v_cfg
                else:
                    no_split = False

            if sigma_next == 0:
                # Terminal step: the (1-sigma_next)/sigma_next split zeroes
                # the eps term regardless of the split, so this is exact.
                x = x0_est
            elif no_split:
                # No genuine CFG++ split active: take the exact same
                # shortcut euler.py uses, so this sampler is BIT-IDENTICAL
                # to it whenever the guidance strategy exposes no uncond
                # branch -- in particular, always at cfg=1.0.
                x = x + (sigma_next - sigma) * v_cfg
            else:
                eps = x + (1.0 - sigma) * v_dir
                x = (1.0 - sigma_next) * x0_est + sigma_next * eps

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0_est)
    finally:
        run_hooks(hooks, "on_end")

    return x
