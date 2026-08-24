"""CFG++ ancestral Euler sampler tests (the maintainer's
validated ComfyUI LTX-2.3 distilled-refine recipe uses ``euler_ancestral_cfg_pp``).

Mirrors ``test_euler_sde.py``'s structure (this sampler degenerates to exactly
``sample_euler_sde`` whenever the guidance strategy exposes no ``last_uncond_v``
-- see the module docstring), plus CFG++-specific hand-computed-formula tests
using a stub :class:`GuidanceStrategy` that exposes a DIFFERENT velocity for
the CFG++ "direction" than for the "target", so the two roles are provably
not conflated.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.euler_ancestral_cfg_pp import (
    sample_euler_ancestral_cfg_pp,
)
from src.platform.runtime.native.sampling.algorithms.euler_sde import sample_euler_sde
from src.platform.runtime.native.sampling.cfg import NoCFG, TrueCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


class _SplitGuidance:
    """Stub :class:`GuidanceStrategy`: returns ``v_cfg`` as the combined
    velocity but exposes a DIFFERENT ``last_uncond_v`` (``v_uncond``), so a
    test can prove the sampler used the right velocity for the right role
    instead of accidentally reading the same tensor for both."""

    zero_init_steps = 0

    def __init__(self, v_cfg: float, v_uncond: float, zero_init_steps: int = 0) -> None:
        self.v_cfg = v_cfg
        self.v_uncond = v_uncond
        self.zero_init_steps = zero_init_steps
        self.last_uncond_v = None
        self.calls = 0

    def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
        self.calls += 1
        if step_index < self.zero_init_steps:
            # Deliberately mirrors TrueCFG's REAL zero-init early-return
            # (cfg.py: `if step_index < self.zero_init_steps: return
            # torch.zeros_like(x)` is its very first line) -- it does NOT
            # touch last_uncond_v, so any PRIOR value is left stale. The
            # sampler's own S9 guard (not this stub) is what must keep that
            # staleness from leaking into the direction used this step.
            return torch.zeros_like(x)
        self.last_uncond_v = torch.full_like(x, self.v_uncond)
        return torch.full_like(x, self.v_cfg)


# --------------------------------------------------------------------------- #
# no-uncond-branch degeneracy: byte-identical to plain ancestral euler_sde
# --------------------------------------------------------------------------- #

def test_no_last_uncond_v_degenerates_to_euler_sde_exactly():
    # NoCFG never sets last_uncond_v, so CFG++'s v_dir falls back to v_cfg --
    # the whole mechanism becomes a no-op and this must match euler_sde bit
    # for bit at the same eta/generator.
    x_init = torch.randn(2, 3, 4)
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])

    gen1 = torch.Generator().manual_seed(11)
    out_cfg_pp = sample_euler_ancestral_cfg_pp(
        _const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen1},
    )
    gen2 = torch.Generator().manual_seed(11)
    out_sde = sample_euler_sde(
        _const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": gen2},
    )
    assert torch.equal(out_cfg_pp, out_sde)


def test_eta_zero_no_split_matches_plain_euler_exactly():
    x_init = torch.randn(1, 5)
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    out_euler = sample_euler(_const_velocity_model(2.1), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_cfg_pp = sample_euler_ancestral_cfg_pp(
        _const_velocity_model(2.1), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 0.0},
    )
    assert torch.equal(out_euler, out_cfg_pp)


def test_true_cfg_scale_one_has_no_uncond_branch_and_degenerates():
    # TrueCFG(scale=1.0) never runs the uncond forward, so last_uncond_v stays
    # None -- same degeneracy as NoCFG, this time through the real strategy.
    x_init = torch.randn(1, 4)
    sigmas = torch.tensor([1.0, 0.5, 0.0])

    gen1 = torch.Generator().manual_seed(3)
    out_cfg_pp = sample_euler_ancestral_cfg_pp(
        _const_velocity_model(1.0), x_init.clone(), sigmas, TrueCFG(1.0), {"v": 1.0}, {"v": 1.0},
        sampler_options={"eta": 1.0, "generator": gen1},
    )
    gen2 = torch.Generator().manual_seed(3)
    out_sde = sample_euler_sde(
        _const_velocity_model(1.0), x_init.clone(), sigmas, TrueCFG(1.0), {"v": 1.0}, {"v": 1.0},
        sampler_options={"eta": 1.0, "generator": gen2},
    )
    assert torch.equal(out_cfg_pp, out_sde)


# --------------------------------------------------------------------------- #
# CFG++ target/direction decoupling: hand-computed formula
# --------------------------------------------------------------------------- #

def test_terminal_step_lands_on_guided_target_regardless_of_direction_mismatch():
    # x0_est = x - sigma*v_cfg must be the EXACT final output at sigma_next==0,
    # even when v_uncond is wildly different from v_cfg (the DDIM-style
    # collapse the module docstring proves -- this is the whole reason CFG++
    # is built on euler_sde's eps/x0 formulation rather than the vendored
    # to_d/get_ancestral_step split).
    x_init = torch.tensor([[10.0]])
    sigmas = torch.tensor([1.0, 0.0])
    guidance = _SplitGuidance(v_cfg=2.0, v_uncond=-500.0)  # deliberately absurd v_uncond
    out = sample_euler_ancestral_cfg_pp(
        None, x_init.clone(), sigmas, guidance, {}, {},
        sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(1)},
    )
    # x0_est = 10 - 1*2 = 8, independent of v_uncond.
    assert torch.allclose(out, torch.tensor([[8.0]]))


def test_deterministic_branch_hand_computed_formula():
    # eta=0.0 (no fresh noise): x_next = (1-sigma_next)*x0_est + sigma_next*eps,
    # eps = x + (1-sigma)*v_uncond, x0_est = x - sigma*v_cfg.
    x_init = torch.tensor([[4.0]])
    sigma, sigma_next = 0.8, 0.3
    sigmas = torch.tensor([sigma, sigma_next])
    v_cfg, v_uncond = 5.0, -2.0
    guidance = _SplitGuidance(v_cfg=v_cfg, v_uncond=v_uncond)

    out = sample_euler_ancestral_cfg_pp(
        None, x_init.clone(), sigmas, guidance, {}, {},
        sampler_options={"eta": 0.0},
    )

    x0_est = 4.0 - sigma * v_cfg
    eps = 4.0 + (1.0 - sigma) * v_uncond
    expected = (1.0 - sigma_next) * x0_est + sigma_next * eps
    assert torch.allclose(out, torch.tensor([[expected]]), atol=1e-6)


def test_multi_step_trajectory_matches_hand_computed_formula():
    # Three steps (1.0 -> 0.6 -> 0.3 -> 0.0), eta=0 (fully deterministic), so
    # every intermediate x is exactly reproducible by hand from the same
    # eps/x0_est recurrence the module docstring derives. The first step's
    # (1-sigma)=0 coefficient makes v_uncond inert there (a property of the
    # formula, not a test gap) -- so the divergence this test is actually
    # checking for shows up from the SECOND step onward.
    x0 = torch.tensor([[4.0]])
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])
    v_cfg = 1.5

    def run(v_uncond):
        guidance = _SplitGuidance(v_cfg=v_cfg, v_uncond=v_uncond)
        return sample_euler_ancestral_cfg_pp(
            None, x0.clone(), sigmas, guidance, {}, {}, sampler_options={"eta": 0.0},
        )

    def hand_computed(v_uncond):
        x = x0.item()
        for sigma, sigma_next in zip(sigmas[:-1].tolist(), sigmas[1:].tolist()):
            x0_est = x - sigma * v_cfg
            if sigma_next == 0.0:
                x = x0_est
            else:
                eps = x + (1.0 - sigma) * v_uncond
                x = (1.0 - sigma_next) * x0_est + sigma_next * eps
        return x

    for v_uncond in (0.5, 9.0):
        out = run(v_uncond)
        assert torch.allclose(out, torch.tensor([[hand_computed(v_uncond)]]), atol=1e-6)

    # And the two v_uncond values must actually produce different trajectories
    # (proving the direction really is read from last_uncond_v, not ignored).
    assert not torch.equal(run(0.5), run(9.0))


def test_zero_init_window_uses_v_cfg_as_direction_not_stale_last_uncond_v():
    # S9 guard (mirrors SkipLayerGuidance): during a zero-init window no
    # uncond forward ran, so last_uncond_v would be stale/None. The sampler
    # must use v_cfg (== 0 during zero-init) as the direction for those
    # steps, NOT silently reuse a leftover value from a previous call.
    x_init = torch.tensor([[3.0]])
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    guidance = _SplitGuidance(v_cfg=0.0, v_uncond=999.0, zero_init_steps=1)
    # Poison last_uncond_v with a stale value from a "previous step" up front.
    guidance.last_uncond_v = torch.tensor([[-777.0]])

    out = sample_euler_ancestral_cfg_pp(
        None, x_init.clone(), sigmas, guidance, {}, {},
        sampler_options={"eta": 0.0},
    )
    # Step 0 (zero-init): v_cfg=0 -> x0_est=3, direction=v_cfg=0 -> eps=3+ (1-1)*0=3
    #   x1 = (1-0.5)*3 + 0.5*3 = 3.
    # Step 1 (normal, but guidance.__call__ still returns v_cfg=0/v_uncond=999
    #   since _SplitGuidance ignores step_index>=zero_init_steps for its own
    #   constant values): x0_est = 3 - 0.5*0 = 3; terminal step -> out = 3.
    assert torch.allclose(out, torch.tensor([[3.0]]))


def test_reproducible_with_seeded_generator():
    x_init = torch.zeros(1, 4)
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    guidance_factory = lambda: TrueCFG(2.0, cfg_zero_star=False)

    gen1 = torch.Generator().manual_seed(42)
    out1 = sample_euler_ancestral_cfg_pp(
        _const_velocity_model(0.0), x_init.clone(), sigmas, guidance_factory(), {"v": 0.3}, {"v": 0.1},
        sampler_options={"eta": 1.0, "generator": gen1},
    )
    gen2 = torch.Generator().manual_seed(42)
    out2 = sample_euler_ancestral_cfg_pp(
        _const_velocity_model(0.0), x_init.clone(), sigmas, guidance_factory(), {"v": 0.3}, {"v": 0.1},
        sampler_options={"eta": 1.0, "generator": gen2},
    )
    assert torch.equal(out1, out2)


def test_invalid_eta_raises():
    x_init = torch.zeros(1, 2)
    sigmas = torch.tensor([1.0, 0.0])
    with pytest.raises(ValueError):
        sample_euler_ancestral_cfg_pp(
            _const_velocity_model(0.0), x_init, sigmas, NoCFG(), {}, None,
            sampler_options={"eta": -0.1},
        )


def test_hooks_fire_once_per_step():
    class Counter(BaseStepHook):
        def __init__(self):
            self.starts = 0
            self.steps = 0
            self.ends = 0

        def on_start(self, total_steps):
            self.starts += 1
            self.total = total_steps

        def on_step(self, step_index, total_steps, x, sigma, denoised_x0):
            self.steps += 1

        def on_end(self):
            self.ends += 1

    counter = Counter()
    sigmas = torch.tensor([1.0, 0.5, 0.0])  # 2 steps
    sample_euler_ancestral_cfg_pp(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter], sampler_options={"eta": 1.0},
    )
    assert counter.starts == 1
    assert counter.steps == 2
    assert counter.total == 2
    assert counter.ends == 1


def test_finite_with_a_real_true_cfg_strategy_end_to_end():
    # Not hand-computed -- just a smoke test that a real (non-stub) guidance
    # strategy with a genuine uncond branch (scale > 1, so the split actually
    # activates -- unlike the recipe's own cfg=1.0, which degenerates to
    # plain ancestral, see test_true_cfg_scale_one_has_no_uncond_branch_and_
    # degenerates above) runs to completion over the maintainer's actual
    # recipe length (9 sigmas) and produces a finite result.
    torch.manual_seed(0)
    x_init = torch.randn(1, 4, 4)
    sigmas = torch.tensor([1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0])

    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    out = sample_euler_ancestral_cfg_pp(
        model_fn, x_init, sigmas, TrueCFG(2.5, cfg_zero_star=False), {"v": 0.05}, {"v": 0.02},
        sampler_options={"eta": 1.0, "generator": torch.Generator().manual_seed(5)},
    )
    assert torch.isfinite(out).all()
