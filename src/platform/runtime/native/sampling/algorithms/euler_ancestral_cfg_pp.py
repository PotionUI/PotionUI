"""Ancestral Euler with the CFG++ velocity/target decoupling.

CFG++ (Chung et al., "CFG++: Manifold-Constrained Classifier Free Guidance
for Diffusion Models", arXiv:2406.08070) observes that plain CFG's guidance
scale also inflates the ODE/SDE STEP SIZE, pushing the trajectory off the
data manifold at the CFG scales (3-4+) that give good prompt adherence. Its
fix: keep the CFG-guided prediction as the STEP'S TARGET (so the final image
is still fully guided), but derive the step's DIRECTION/carried-forward noise
from the plain UNCONDITIONAL prediction instead of the guided one. This file
re-derives that idea (paper + the module's own algebra below), NOT a port of
any sampler implementation -- ComfyUI's own ``sample_euler_ancestral_cfg_pp``
is GPL-3.0 (see ``.cfg.SkipLayerGuidance``'s docstring for the same
re-derive-don't-port stance on ComfyUI-only techniques) and is deliberately
not consulted here.

Re-derivation, extending :mod:`.euler_sde`'s own eps/x0 formulation (chosen
over vendored k-diffusion's ``to_d``/``get_ancestral_step`` sigma_up/sigma_down
split -- see below for why): for a flow-matching CONST model, a velocity
prediction ``v`` implies a clean-latent estimate ``x0 = x - sigma*v`` and,
via the flow interpolation ``x = (1-sigma)*x0 + sigma*eps``, an implied noise
``eps = x0 + v = x + (1-sigma)*v`` (see ``euler_sde.py``'s module docstring
for the full algebra; identical here). Plain ancestral Euler uses the SAME
``v`` for both quantities. CFG++ instead uses two different velocities:

* ``v_cfg`` (the guidance strategy's normal combined output) for the TARGET:
  ``x0_est = x - sigma * v_cfg``.
* ``v_uncond`` (the strategy's raw, un-combined uncond-branch prediction --
  exposed as ``guidance.last_uncond_v``, see ``TrueCFG``) for the DIRECTION:
  ``eps = x + (1 - sigma) * v_uncond``.

Then the same variance-preserving eta-mix as ``euler_sde.py``:
``eps_mix = sqrt(1-eta^2)*eps + eta*eps_fresh``, ``x_next = (1-sigma_next)*
x0_est + sigma_next*eps_mix``. This formulation (unlike the vendored
``to_d``/``get_ancestral_step`` split) collapses EXACTLY to ``x0_est`` at the
terminal step (``sigma_next == 0`` zeroes the ``eps_mix`` term outright,
regardless of ``eta`` or the size of the ``v_cfg``/``v_uncond`` mismatch) --
the DDIM-style anchoring the CFG++ paper itself uses, and the reason this
module builds on ``euler_sde``'s parameterization rather than the vendored
sigma_up/sigma_down one (which does NOT collapse cleanly here: its target
anchor is ``x``, not ``x0_est``, so a nonzero ``dt`` at the final ancestral
sub-step would carry a residual ``sigma * v_uncond`` term past the clean
estimate).

Guidance strategies with no uncond branch (``EmbeddedGuidance``, ``NoCFG``,
or ``TrueCFG`` at ``scale == 1.0``) expose no ``last_uncond_v`` (or expose
``None``); this sampler falls back to ``v_uncond = v_cfg``, which makes the
whole CFG++ mechanism a no-op and reduces byte-for-byte to plain ancestral
Euler (``euler_sde`` with the same ``eta``) -- so selecting this sampler is
always safe, even against a single-pass guidance mode.

First-party delta: checked Lightricks' own inference package
(``packages/ltx-pipelines`` in ``github.com/Lightricks/LTX-2``, Apache-2.0)
for their distilled-pass implementation. Their sigma schedule
(``DISTILLED_SIGMA_VALUES``) is a digit-for-digit match to the recipe this
sampler is meant to run, but their own sampler (``SimpleDenoiser`` /
``euler_denoising_loop`` in that package) runs NO classifier-free guidance at
all for the distilled pass and, per the function's own name and the absence
of any noise-sampler code in its calling ``blocks.py``, is **plain
deterministic Euler** -- not ancestral. This sampler's ancestral component
(default ``eta=1.0``) is therefore a community (ComfyUI-workflow) choice
layered on top of Lightricks' schedule, not something Lightricks' own app
does; at ``cfg=1.0`` (matching their no-CFG distilled pass) this sampler's
CFG++ split is already a no-op, but its ancestral noise injection still
differs from Lightricks' path unless a caller also sets ``eta=0`` via
``sampler_options``.
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
def sample_euler_ancestral_cfg_pp(
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
    """Ancestral Euler, CFG++ target/direction decoupling. See module docstring.

    Same signature/semantics as :func:`~.euler_sde.sample_euler_sde` (indeed,
    with no uncond branch available this degenerates to exactly that
    sampler). ``sampler_options``:

    * ``eta`` (default ``1.0``) -- fraction of the implied per-step noise
      replaced with fresh noise, in ``[0, 1]``; same meaning as
      ``euler_sde``'s ``eta``.
    * ``generator`` (default ``None``) -- seeded ``torch.Generator`` for
      reproducible fresh-noise draws (see ``euler_sde.py``).
    """
    opts = sampler_options or {}
    eta = float(opts.get("eta", 1.0))
    if not (0.0 <= eta <= 1.0):
        raise ValueError(f"eta must be in [0, 1], got {eta}")
    generator = opts.get("generator")

    total_steps = len(sigmas) - 1
    run_hooks(hooks, "on_start", total_steps)

    # Mirrors SkipLayerGuidance's own guard: during a TrueCFG zero-init
    # window the guidance call below returns a deliberate zero velocity
    # without ever running an uncond forward this step, so `last_uncond_v`
    # would still hold a STALE value from a prior step. Route those steps
    # around the CFG++ split entirely (v_cfg IS v_uncond IS 0, so this is a
    # pure no-op vs. treating it as "no split available").
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
                # the eps term regardless of eta, so this is exact for both
                # the CFG++ and plain-ancestral cases -- no need to touch
                # v_dir or draw noise at all.
                x = x0_est
            elif eta == 0.0 and no_split:
                # No genuine CFG++ split active (degenerate to plain Euler):
                # take the exact same shortcut euler.py/euler_sde.py use, so
                # this sampler is BIT-IDENTICAL to them whenever a caller's
                # guidance strategy exposes no uncond branch -- not merely
                # numerically close via the general affine formula below
                # (same result up to float rounding, different operation
                # order).
                x = x + (sigma_next - sigma) * v_cfg
            elif eta == 0.0:
                eps = x + (1.0 - sigma) * v_dir
                x = (1.0 - sigma_next) * x0_est + sigma_next * eps
            else:
                eps = x + (1.0 - sigma) * v_dir
                eps_fresh = _fresh_noise(x, generator)
                kept = math.sqrt(max(1.0 - eta * eta, 0.0))
                eps_mix = kept * eps + eta * eps_fresh
                x = (1.0 - sigma_next) * x0_est + sigma_next * eps_mix

            run_hooks(hooks, "on_step", i, total_steps, x, float(sigma), x0_est)
    finally:
        run_hooks(hooks, "on_end")

    return x
