"""Deterministic Euler CFG++ sampler tests (LTX-2.3's own distilled-refine
recipe is deterministic, no CFG -- see docs/models/ltx.md's first-party
validation section; this sampler replaces euler_ancestral_cfg_pp as the
Distilled speed profile's default).

Mirrors ``test_euler_ancestral_cfg_pp.py``'s hand-computed-formula structure,
minus the ancestral/eta machinery, plus the load-bearing CFG=1 <-> plain-euler
proof and a determinism check the ancestral sibling can't offer (its default
eta=1.0 draws fresh noise every step).
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.algorithms.euler_ancestral_cfg_pp import (
    sample_euler_ancestral_cfg_pp,
)
from src.platform.runtime.native.sampling.algorithms.euler_cfg_pp import sample_euler_cfg_pp
from src.platform.runtime.native.sampling.cfg import NoCFG, TrueCFG
from src.platform.runtime.native.sampling.hooks import BaseStepHook


def _const_velocity_model(v0):
    def model_fn(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_fn


class _SplitGuidance:
    """Stub GuidanceStrategy exposing a different ``last_uncond_v`` than the
    combined ``v_cfg`` it returns -- proves the sampler reads the right
    velocity for the right role (see euler_ancestral_cfg_pp's test file for
    the identical stub)."""

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
            return torch.zeros_like(x)
        self.last_uncond_v = torch.full_like(x, self.v_uncond)
        return torch.full_like(x, self.v_cfg)


# --------------------------------------------------------------------------- #
# CFG=1 (no uncond branch) <-> plain euler: the load-bearing recipe proof
# --------------------------------------------------------------------------- #

def test_cfg_one_true_cfg_degenerates_to_plain_euler_exactly():
    # TrueCFG(scale=1.0) never runs the uncond forward -- last_uncond_v stays
    # None -- so v_dir falls back to v_cfg and the no_split shortcut fires:
    # bit-identical to sample_euler, same seed/sigmas, no tolerance needed.
    x_init = torch.randn(2, 3, 4)
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])

    out_euler = sample_euler(
        _const_velocity_model(1.3), x_init.clone(), sigmas, NoCFG(), {}, None,
    )
    out_cfg_pp = sample_euler_cfg_pp(
        _const_velocity_model(1.3), x_init.clone(), sigmas, TrueCFG(1.0), {"v": 1.3}, {"v": 1.3},
    )
    assert torch.equal(out_euler, out_cfg_pp)


def test_no_cfg_strategy_degenerates_to_plain_euler_exactly():
    x_init = torch.tensor([[10.0, 20.0]])
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])
    out_euler = sample_euler(_const_velocity_model(2.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_cfg_pp = sample_euler_cfg_pp(_const_velocity_model(2.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    assert torch.equal(out_euler, out_cfg_pp)


# --------------------------------------------------------------------------- #
# cfg > 1: genuine CFG++ split differs from both plain euler AND the
# ancestral sibling, and is exactly repeatable (no noise draw)
# --------------------------------------------------------------------------- #

def test_cfg_above_one_differs_from_plain_euler():
    x_init = torch.tensor([[4.0]])
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])
    guidance = _SplitGuidance(v_cfg=1.5, v_uncond=0.2)
    out_cfg_pp = sample_euler_cfg_pp(None, x_init.clone(), sigmas, guidance, {}, {})
    out_euler = sample_euler(_const_velocity_model(1.5), x_init.clone(), sigmas, NoCFG(), {}, None)
    assert not torch.allclose(out_cfg_pp, out_euler)


def test_cfg_above_one_differs_from_ancestral_sibling_no_noise_injected():
    # Same v_cfg/v_uncond split fed to both samplers with the ancestral
    # sibling's eta pinned to 0 would make them equal (they share the same
    # eta=0 formula) -- the actual point of this sampler existing is that its
    # DEFAULT has no eta/generator knob at all and never diverges from a
    # deterministic re-run, unlike the ancestral sampler's own default
    # (eta=1.0, fresh noise every step) which this test exercises directly.
    x_init = torch.tensor([[4.0]])
    sigmas = torch.tensor([1.0, 0.6, 0.3, 0.0])

    guidance_det = _SplitGuidance(v_cfg=1.5, v_uncond=0.2)
    out_cfg_pp = sample_euler_cfg_pp(None, x_init.clone(), sigmas, guidance_det, {}, {})

    guidance_anc = _SplitGuidance(v_cfg=1.5, v_uncond=0.2)
    gen = torch.Generator().manual_seed(7)
    out_ancestral = sample_euler_ancestral_cfg_pp(
        None, x_init.clone(), sigmas, guidance_anc, {}, {},
        sampler_options={"eta": 1.0, "generator": gen},
    )
    assert not torch.allclose(out_cfg_pp, out_ancestral)


def test_deterministic_repeatable_across_two_runs_same_seed():
    # No RNG involved at all -- two independent runs over the identical
    # inputs must be bit-for-bit equal (the whole point of "deterministic",
    # unlike euler_ancestral_cfg_pp which needs a seeded generator for this).
    x_init = torch.randn(1, 4, 4)
    sigmas = torch.tensor([1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0])

    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    def run():
        return sample_euler_cfg_pp(
            model_fn, x_init.clone(), sigmas, TrueCFG(2.5, cfg_zero_star=False), {"v": 0.05}, {"v": 0.02},
        )

    out1 = run()
    out2 = run()
    assert torch.equal(out1, out2)


# --------------------------------------------------------------------------- #
# hand-computed formula (mirrors euler_ancestral_cfg_pp's eta=0 branch test)
# --------------------------------------------------------------------------- #

def test_deterministic_branch_hand_computed_formula():
    x_init = torch.tensor([[4.0]])
    sigma, sigma_next = 0.8, 0.3
    sigmas = torch.tensor([sigma, sigma_next])
    v_cfg, v_uncond = 5.0, -2.0
    guidance = _SplitGuidance(v_cfg=v_cfg, v_uncond=v_uncond)

    out = sample_euler_cfg_pp(None, x_init.clone(), sigmas, guidance, {}, {})

    x0_est = 4.0 - sigma * v_cfg
    eps = 4.0 + (1.0 - sigma) * v_uncond
    expected = (1.0 - sigma_next) * x0_est + sigma_next * eps
    assert torch.allclose(out, torch.tensor([[expected]]), atol=1e-6)


def test_terminal_step_lands_on_guided_target_regardless_of_direction_mismatch():
    x_init = torch.tensor([[10.0]])
    sigmas = torch.tensor([1.0, 0.0])
    guidance = _SplitGuidance(v_cfg=2.0, v_uncond=-500.0)
    out = sample_euler_cfg_pp(None, x_init.clone(), sigmas, guidance, {}, {})
    assert torch.allclose(out, torch.tensor([[8.0]]))


def test_zero_init_window_uses_v_cfg_as_direction_not_stale_last_uncond_v():
    x_init = torch.tensor([[3.0]])
    sigmas = torch.tensor([1.0, 0.5, 0.0])
    guidance = _SplitGuidance(v_cfg=0.0, v_uncond=999.0, zero_init_steps=1)
    guidance.last_uncond_v = torch.tensor([[-777.0]])
    out = sample_euler_cfg_pp(None, x_init.clone(), sigmas, guidance, {}, {})
    assert torch.allclose(out, torch.tensor([[3.0]]))


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
    sample_euler_cfg_pp(
        _const_velocity_model(1.0), torch.zeros(1, 2), sigmas, NoCFG(), {}, None,
        hooks=[counter],
    )
    assert counter.starts == 1
    assert counter.steps == 2
    assert counter.total == 2
    assert counter.ends == 1


def test_sampler_options_ignored_without_error():
    # Part of the uniform SAMPLERS contract -- accepted, has no effect.
    x_init = torch.tensor([[1.0]])
    sigmas = torch.tensor([1.0, 0.0])
    out_a = sample_euler_cfg_pp(_const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None)
    out_b = sample_euler_cfg_pp(
        _const_velocity_model(1.0), x_init.clone(), sigmas, NoCFG(), {}, None,
        sampler_options={"eta": 1.0, "generator": None},
    )
    assert torch.equal(out_a, out_b)


def test_finite_with_a_real_true_cfg_strategy_end_to_end():
    torch.manual_seed(0)
    x_init = torch.randn(1, 4, 4)
    sigmas = torch.tensor([1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0])

    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    out = sample_euler_cfg_pp(
        model_fn, x_init, sigmas, TrueCFG(2.5, cfg_zero_star=False), {"v": 0.05}, {"v": 0.02},
    )
    assert torch.isfinite(out).all()
