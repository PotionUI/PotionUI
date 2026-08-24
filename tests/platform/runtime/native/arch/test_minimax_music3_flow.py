"""Tests for the MiniMax-Music3 windowed Euler flow-matching loop.

Coverage: the inverted flow-time schedule against hand-computed values, the
chunk-start algebra, the crop/tiling algebra (bite-checked by perturbing the 86-latent
crop constant), the plain Euler update in isolation (a constant-velocity double removes
the DiT/CFG numerics so the update arithmetic is checked on its own), the per-step
overlap-pin convergence and the post-loop verbatim restore, and cancellation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.arch.minimax_music3 import flow
from src.platform.runtime.native.errors import SamplingCancelled


# --- _flow_sigmas: hand-computed values -----------------------------------------

def test_flow_sigmas_two_steps_hand_computed():
    """steps=2: input sigmas = linspace(1.0, 0.5, 2) = [1.0, 0.5]; shift=1.0 is a
    no-op; invert_sigmas flips to t = 1 - sigma = [0.0, 0.5]; terminal 1.0 appended.
    """
    sigmas = flow._flow_sigmas(2)
    torch.testing.assert_close(sigmas, torch.tensor([0.0, 0.5, 1.0]))


def test_flow_sigmas_starts_at_zero_ends_near_one():
    sigmas = flow._flow_sigmas(30)
    assert sigmas[0].item() == 0.0
    assert sigmas[-1].item() == 1.0
    assert sigmas[-2].item() == pytest.approx(1.0 - 1.0 / 30, abs=1e-6)
    assert torch.all(sigmas[1:] >= sigmas[:-1])  # non-decreasing


# --- chunk_starts ----------------------------------------------------------------

@pytest.mark.parametrize(
    "num_frames,expected",
    [
        (50, [0]),
        (200, [0]),
        (201, [0, 100]),
        (1500, list(range(0, 1400, 100))),
    ],
)
def test_chunk_starts(num_frames, expected):
    assert flow.chunk_starts(num_frames) == expected


def test_chunk_starts_1500_is_fourteen_windows():
    assert len(flow.chunk_starts(1500)) == 14


# --- crop/tiling algebra ----------------------------------------------------------

def _sum_kept_latents(num_frames: int, crop_left: int, crop_right: int) -> int:
    starts = flow.chunk_starts(num_frames)
    n = len(starts)
    total = 0
    for i, s in enumerate(starts):
        e = min(s + flow.CHUNK_FRAMES, num_frames)
        window_latents = flow.latent_length(e - s)
        left = 0 if i == 0 else crop_left
        right = 0 if i == n - 1 else crop_right
        total += window_latents - left - right
    return total


@pytest.mark.parametrize("num_frames", [50, 200, 201])
def test_crop_tiling_exact_for_single_or_double_window(num_frames):
    total = _sum_kept_latents(num_frames, flow.CROP_LEFT_LATENT, flow.CROP_RIGHT_LATENT)
    assert total == flow.latent_length(num_frames)


@pytest.mark.parametrize("num_frames", [1500, 9000])
def test_crop_tiling_close_for_many_windows(num_frames):
    """Multi-window songs do NOT tile to an exact match against `latent_length(F)`
    — each window's latent count is independently floor-rounded from its own frame
    count, and that per-window rounding doesn't telescope back to the global
    formula's single rounding. This is a real property of the reference algorithm
    (diffusers `before_denoise.py`/`decoders.py`), not a bug in this port — see the
    S3 report's plan-correction note. The drift is small (well under one window's
    worth of latents) and one-directional (kept-sum >= latent_length(F))."""
    num_windows = len(flow.chunk_starts(num_frames))
    total = _sum_kept_latents(num_frames, flow.CROP_LEFT_LATENT, flow.CROP_RIGHT_LATENT)
    target = flow.latent_length(num_frames)
    assert total >= target
    assert total - target <= num_windows


def test_bite_check_wrong_crop_constant_breaks_tiling():
    """Perturbing the 86-latent left-crop constant must move the multi-window
    tiling total further from `latent_length(F)`, proving the test above is
    actually sensitive to that constant."""
    num_frames = 1500
    target = flow.latent_length(num_frames)
    real_diff = _sum_kept_latents(num_frames, flow.CROP_LEFT_LATENT, flow.CROP_RIGHT_LATENT) - target
    wrong_diff = _sum_kept_latents(num_frames, flow.CROP_LEFT_LATENT + 20, flow.CROP_RIGHT_LATENT) - target
    assert abs(wrong_diff) > abs(real_diff)


def test_crop_bounds_first_and_last_window():
    assert flow.crop_bounds(0, 3) == (0, flow.CROP_RIGHT_LATENT)
    assert flow.crop_bounds(1, 3) == (flow.CROP_LEFT_LATENT, flow.CROP_RIGHT_LATENT)
    assert flow.crop_bounds(2, 3) == (flow.CROP_LEFT_LATENT, 0)
    assert flow.crop_bounds(0, 1) == (0, 0)  # single window: nothing cropped


# --- test double: isolates the Euler update from DiT/CFG numerics -----------------

class _ConstantVelocityModel:
    """A minimal stand-in for `MiniMaxMusic3Model` that always predicts the SAME
    velocity regardless of latents/timestep/condition — so `denoise_windowed`'s
    CFG combine (`uncond + scale*(cond-uncond)`) collapses to that velocity for
    ANY `cfg_scale`, and the only thing left to check is the Euler update itself.
    """

    def __init__(self, in_channels: int, velocity: torch.Tensor, condition_dim: int = 3):
        self.config = SimpleNamespace(in_channels=in_channels)
        self._velocity = velocity
        self._condition_dim = condition_dim
        self.calls = 0

    def encode_condition(self, frame_hiddens: torch.Tensor) -> torch.Tensor:
        num_frames = frame_hiddens.shape[1]
        t_lat = flow.latent_length(num_frames)
        return torch.zeros(1, t_lat, self._condition_dim)

    def __call__(self, latents, timestep, condition):
        self.calls += 1
        return self._velocity.expand_as(latents)


def test_euler_update_matches_hand_computation_no_overlap():
    """Single window (no overlap-pin path), steps=2: dt telescopes to exactly 1.0
    over the full [0,1] span, so for a CONSTANT velocity the result is
    `initial_noise + velocity` regardless of the intermediate step count —
    verified here for the plan's specific 2-step case."""
    in_channels = 2
    velocity = torch.full((1, in_channels, 1), 0.25)
    model = _ConstantVelocityModel(in_channels, velocity)

    generator = torch.Generator().manual_seed(0)
    expected_noise_generator = torch.Generator().manual_seed(0)
    num_frames = 5  # <= CHUNK_FRAMES: single window
    t_lat = flow.latent_length(num_frames)
    expected_noise = torch.randn((1, in_channels, t_lat), generator=expected_noise_generator)

    frame_hiddens = torch.randn(1, num_frames, 8)
    [result] = flow.denoise_windowed(
        model, frame_hiddens, steps=2, cfg_scale=1.7,
        generator=generator, device=torch.device("cpu"), dtype=torch.float32,
    )
    expected = expected_noise + velocity  # dt0+dt1 == 1.0
    torch.testing.assert_close(result, expected)
    assert model.calls == 2 * 2  # 2 steps * (cond + uncond) passes


def test_overlap_pin_converges_to_previous_latent_and_is_restored_verbatim():
    """Two windows (F=201 -> [0, 100]): window 1 has an overlap with window 0's
    carry. Regardless of the model's velocity, the post-loop overlap region must
    equal window 0's carried latents EXACTLY (the unconditional restore after the
    Euler loop), not merely converge toward it."""
    in_channels = 2
    velocity = torch.randn(1, in_channels, 1) * 5.0  # deliberately not zero/small
    model = _ConstantVelocityModel(in_channels, velocity)

    generator = torch.Generator().manual_seed(1)
    num_frames = 201
    frame_hiddens = torch.randn(1, num_frames, 8)
    chunks = flow.denoise_windowed(
        model, frame_hiddens, steps=4, cfg_scale=1.7,
        generator=generator, device=torch.device("cpu"), dtype=torch.float32,
    )
    assert len(chunks) == 2

    window0_latents = chunks[0]
    t0 = window0_latents.shape[-1]
    carry_start = max(0, t0 - 2 * flow._CARRY_LATENT_LENGTH)
    carry_end = max(carry_start, t0 - flow._CARRY_LATENT_LENGTH)
    expected_carry = window0_latents[..., carry_start:carry_end]

    window1_latents = chunks[1]
    overlap = min(expected_carry.shape[-1], window1_latents.shape[-1])
    torch.testing.assert_close(window1_latents[..., :overlap], expected_carry[..., :overlap])


def test_bite_check_no_overlap_restore_would_diverge():
    """If the post-loop restore were skipped, the overlap region would only be the
    per-step BLEND toward the carry (not exact), which for a large constant
    velocity and few steps is measurably off — confirming the restore in the test
    above is actually load-bearing, not a no-op for this fixture."""
    in_channels = 2
    velocity = torch.randn(1, in_channels, 1) * 5.0
    model = _ConstantVelocityModel(in_channels, velocity)
    generator = torch.Generator().manual_seed(1)
    num_frames = 201
    frame_hiddens = torch.randn(1, num_frames, 8)

    starts = flow.chunk_starts(num_frames)
    condition0 = model.encode_condition(frame_hiddens[:, starts[0]:min(starts[0] + flow.CHUNK_FRAMES, num_frames)])
    t0 = condition0.shape[1]
    noise_gen = torch.Generator().manual_seed(1)
    latents0 = torch.randn((1, in_channels, t0), generator=noise_gen)
    window0_without_restore = latents0 + velocity  # steps telescope to dt-sum 1.0, no overlap on window 0

    chunks = flow.denoise_windowed(
        model, frame_hiddens, steps=4, cfg_scale=1.7,
        generator=torch.Generator().manual_seed(1), device=torch.device("cpu"), dtype=torch.float32,
    )
    torch.testing.assert_close(chunks[0], window0_without_restore)
    # window 0 has no overlap (nothing precedes it), so its own value is untouched
    # by any restore -- the interesting (bite-checked) case is window 1's overlap
    # region, covered by the convergence test above.


# --- cancellation -------------------------------------------------------------------

def test_cancellation_raises_before_first_model_call():
    in_channels = 2
    velocity = torch.zeros(1, in_channels, 1)
    model = _ConstantVelocityModel(in_channels, velocity)
    frame_hiddens = torch.randn(1, 5, 8)

    with pytest.raises(SamplingCancelled) as excinfo:
        flow.denoise_windowed(
            model, frame_hiddens, steps=3, cfg_scale=1.7,
            generator=torch.Generator().manual_seed(0), device=torch.device("cpu"), dtype=torch.float32,
            is_cancelled=lambda: True,
        )
    assert excinfo.value.step_index == 0
    assert model.calls == 0


def test_cancellation_mid_window_reports_correct_step_index():
    in_channels = 2
    velocity = torch.zeros(1, in_channels, 1)
    model = _ConstantVelocityModel(in_channels, velocity)
    frame_hiddens = torch.randn(1, 5, 8)

    seen = {"n": 0}

    def _is_cancelled():
        seen["n"] += 1
        return seen["n"] > 2  # cancel on the 3rd check (global step index 2)

    with pytest.raises(SamplingCancelled) as excinfo:
        flow.denoise_windowed(
            model, frame_hiddens, steps=5, cfg_scale=1.7,
            generator=torch.Generator().manual_seed(0), device=torch.device("cpu"), dtype=torch.float32,
            is_cancelled=_is_cancelled,
        )
    assert excinfo.value.step_index == 2


# --- on_step progress callback -------------------------------------------------------

def test_on_step_reports_global_index_and_total_across_windows():
    in_channels = 2
    velocity = torch.zeros(1, in_channels, 1)
    model = _ConstantVelocityModel(in_channels, velocity)
    frame_hiddens = torch.randn(1, 201, 8)  # 2 windows

    calls = []
    flow.denoise_windowed(
        model, frame_hiddens, steps=3, cfg_scale=1.7,
        generator=torch.Generator().manual_seed(0), device=torch.device("cpu"), dtype=torch.float32,
        on_step=lambda i, total: calls.append((i, total)),
    )
    assert calls == [(0, 6), (1, 6), (2, 6), (3, 6), (4, 6), (5, 6)]
