"""Tests for the MiniMax-H3 rectified-flow Euler scheduler math.

Per the port brief: the reference math (`scheduling_minimax_h3.py`'s sigma
grid, `t = 1 - sigma` convention, data-ward `+` velocity, and the `x_t`/`x0`
Euler blend) is re-implemented INDEPENDENTLY here rather than re-using
`schedule.py`'s own functions for the "reference" side, and a full toy
trajectory (both video and audio streams, a few steps, a fixed fake
velocity model) is run through BOTH implementations and compared to ~1e-5 --
the numerical-equivalence test the brief asks for. CPU-only.
"""

from __future__ import annotations

import pytest
import torch

from src.pipelines.pipes.generator.video_minimax_h3.schedule import (
    AUDIO_SHIFT,
    VIDEO_SHIFT,
    build_sigma_schedule,
    data_estimate,
    euler_step,
    parse_manual_sigmas,
    resolve_schedules,
)


# -- independent reference implementation -------------------------------------

def _ref_sigma_schedule(num_inference_steps: int, shift: float) -> tuple[torch.Tensor, torch.Tensor]:
    base = torch.linspace(1.0, 0.0, num_inference_steps + 1, dtype=torch.float32)
    sigmas = shift * base / (1 + (shift - 1) * base)
    sigmas = torch.unique_consecutive(sigmas)
    timesteps = 1.0 - sigmas[:-1]
    return sigmas, timesteps


def _ref_euler_step(model_output, timestep, sample, sigma, sigma_next, *, negate_velocity: bool = False):
    v = -model_output if negate_velocity else model_output
    sigma_from_timestep = 1.0 - float(timestep)
    denoised = sample + sigma_from_timestep * v
    ratio = float(sigma_next) / float(sigma)
    return ratio * sample + (1.0 - ratio) * denoised


# -- sigma schedule -------------------------------------------------------------

def test_sigma_schedule_matches_independent_reference():
    for shift in (VIDEO_SHIFT, AUDIO_SHIFT, 1.0, 7.5):
        got = build_sigma_schedule(20, shift)
        ref_sigmas, ref_timesteps = _ref_sigma_schedule(20, shift)
        torch.testing.assert_close(got.sigmas, ref_sigmas, rtol=0, atol=0)
        torch.testing.assert_close(got.timesteps, ref_timesteps, rtol=0, atol=0)


def test_sigma_schedule_matches_the_modeltc_reference_vectors():
    """The external anchor for what a "step" MEANS.

    ModelTC's Minimax-H3-Turbo README fixes the unshifted grid for NFE = N at
    `q_i = (N - i)/N`, `i = 0..N-1`, plus the terminal 0, and prints the
    NFE = 4 schedules outright: video `[1, 0.9730, 0.9231, 0.8] -> 0` at
    shift 12, audio `[1, 0.9, 0.75, 0.5] -> 0` at shift 3.

    DELIBERATE CHANGE 2026-08-11: `steps` used to be read as the GRID size,
    which ran `steps - 1` evaluations on knots that matched no reference
    (`steps=4` gave video `[1, 0.96, 0.857, 0]`). Do not "fix" this back --
    the vectors below are quoted from the reference, not regenerated from
    this module.
    """
    video = build_sigma_schedule(4, VIDEO_SHIFT)
    audio = build_sigma_schedule(4, AUDIO_SHIFT)

    assert video.sigmas.tolist() == pytest.approx([1.0, 0.972973, 0.923077, 0.8, 0.0], abs=1e-5)
    assert audio.sigmas.tolist() == pytest.approx([1.0, 0.9, 0.75, 0.5, 0.0], abs=1e-5)
    assert video.timesteps.numel() == audio.timesteps.numel() == 4


@pytest.mark.parametrize("steps", (1, 2, 4, 8, 24))
def test_sigma_schedule_follows_the_modeltc_quantile_formula(steps):
    """`q_i = (steps - i)/steps` plus the terminal 0, pushed through the
    shift -- computed here from the formula, not from `build_t_grid`."""
    for shift in (VIDEO_SHIFT, AUDIO_SHIFT):
        knots = [(steps - i) / steps for i in range(steps)] + [0.0]
        expected = [shift * q / (1 + (shift - 1) * q) for q in knots]
        got = build_sigma_schedule(steps, shift)
        assert got.sigmas.tolist() == pytest.approx(expected, abs=1e-6)
        assert got.timesteps.numel() == steps


def test_steps_are_evaluations_so_the_schedule_drives_exactly_that_many():
    for steps in (1, 2, 3, 7, 24, 50):
        assert build_sigma_schedule(steps, VIDEO_SHIFT).timesteps.numel() == steps


def test_sigma_schedule_terminal_sigma_is_exactly_zero():
    got = build_sigma_schedule(24, VIDEO_SHIFT)
    assert float(got.sigmas[-1]) == 0.0


def test_sigma_schedule_step_count_is_grid_points_minus_one():
    got = build_sigma_schedule(24, VIDEO_SHIFT)
    assert got.timesteps.numel() == got.sigmas.numel() - 1
    assert got.timesteps.numel() == 24


def test_video_and_audio_schedules_share_step_count_different_values():
    video = build_sigma_schedule(16, VIDEO_SHIFT)
    audio = build_sigma_schedule(16, AUDIO_SHIFT)
    assert video.timesteps.numel() == audio.timesteps.numel()
    assert not torch.allclose(video.sigmas, audio.sigmas)


# -- manual sigmas: parsing ------------------------------------------------

def test_parse_manual_sigmas_accepts_commas_and_whitespace():
    for raw in ("1.0, 0.75, 0.4, 0.0", "1.0,0.75,0.4,0.0", " 1.0  0.75\t0.4 0.0 ", "1.0, 0.75 0.4,0.0"):
        got = parse_manual_sigmas(raw)
        assert got.sigmas.tolist() == pytest.approx([1.0, 0.75, 0.4, 0.0])


def test_parse_manual_sigmas_derives_timesteps_the_same_way_build_does():
    got = parse_manual_sigmas("0.9, 0.5, 0.0")
    torch.testing.assert_close(got.timesteps, 1.0 - got.sigmas[:-1], rtol=0, atol=0)
    # N grid values drive N-1 model evaluations, same contract as the computed
    # schedule -- this is what keeps the loop code path identical.
    assert got.timesteps.numel() == got.sigmas.numel() - 1


def test_parse_manual_sigmas_allows_a_head_below_one():
    got = parse_manual_sigmas("0.6, 0.3, 0.0")
    assert float(got.sigmas[0]) == pytest.approx(0.6)


@pytest.mark.parametrize("raw, rule", [
    ("", "at least 2"),
    ("0.0", "at least 2"),
    ("0.5, 0.5, 0.0", "strictly decreasing"),
    ("0.5, 0.7, 0.0", "strictly decreasing"),
    ("1.2, 0.5, 0.0", r"\[0, 1\]"),
    ("1.0, -0.1, 0.0", r"\[0, 1\]"),
    ("1.0, 0.5, 0.2", "must be exactly 0.0"),
    ("1.0, 0.5, 1e-9", "must be exactly 0.0"),
    ("1.0, banana, 0.0", "expected comma-separated numbers"),
])
def test_parse_manual_sigmas_rejects_invalid_schedules(raw, rule):
    with pytest.raises(ValueError, match=rule):
        parse_manual_sigmas(raw)


def test_parse_manual_sigmas_error_names_the_field():
    with pytest.raises(ValueError, match="'manual_audio_sigmas'"):
        parse_manual_sigmas("1.0, 0.5", label="'manual_audio_sigmas'")


def test_manual_string_reproduces_a_computed_schedule_bit_identically():
    computed = build_sigma_schedule(9, VIDEO_SHIFT)
    spelled_out = ", ".join(repr(float(v)) for v in computed.sigmas)
    got = parse_manual_sigmas(spelled_out)
    torch.testing.assert_close(got.sigmas, computed.sigmas, rtol=0, atol=0)
    torch.testing.assert_close(got.timesteps, computed.timesteps, rtol=0, atol=0)


# -- manual sigmas: resolution against the two streams ----------------------

def test_resolve_schedules_without_overrides_matches_the_computed_pair():
    video, audio = resolve_schedules(12)
    torch.testing.assert_close(video.sigmas, build_sigma_schedule(12, VIDEO_SHIFT).sigmas, rtol=0, atol=0)
    torch.testing.assert_close(audio.sigmas, build_sigma_schedule(12, AUDIO_SHIFT).sigmas, rtol=0, atol=0)


def test_resolve_schedules_video_override_fills_audio_at_the_matching_count():
    video, audio = resolve_schedules(24, manual_video="1.0, 0.8, 0.55, 0.3, 0.0")
    assert video.sigmas.tolist() == pytest.approx([1.0, 0.8, 0.55, 0.3, 0.0])
    # 5 grid values = 4 evaluations -> the audio side is computed at 4 STEPS,
    # with its OWN shift, and `steps=24` is ignored outright.
    torch.testing.assert_close(audio.sigmas, build_sigma_schedule(4, AUDIO_SHIFT).sigmas, rtol=0, atol=0)
    assert video.timesteps.numel() == audio.timesteps.numel() == 4
    assert audio.sigmas.numel() == 5


def test_resolve_schedules_audio_override_fills_video_at_the_matching_count():
    video, audio = resolve_schedules(24, manual_audio="1.0, 0.8, 0.55, 0.3, 0.0")
    assert audio.sigmas.tolist() == pytest.approx([1.0, 0.8, 0.55, 0.3, 0.0])
    torch.testing.assert_close(video.sigmas, build_sigma_schedule(4, VIDEO_SHIFT).sigmas, rtol=0, atol=0)
    assert video.sigmas.numel() == 5


def test_resolve_schedules_accepts_matching_pair_of_overrides():
    video, audio = resolve_schedules(24, manual_video="1.0, 0.6, 0.0", manual_audio="0.9, 0.4, 0.0")
    assert video.sigmas.tolist() == pytest.approx([1.0, 0.6, 0.0])
    assert audio.sigmas.tolist() == pytest.approx([0.9, 0.4, 0.0])


def test_resolve_schedules_rejects_mismatched_override_lengths():
    with pytest.raises(ValueError, match="same number of steps"):
        resolve_schedules(24, manual_video="1.0, 0.6, 0.0", manual_audio="0.9, 0.6, 0.4, 0.0")


def test_resolve_schedules_treats_blank_and_whitespace_as_no_override():
    for blank in ("", "   ", "\n"):
        video, audio = resolve_schedules(7, manual_video=blank, manual_audio=blank)
        torch.testing.assert_close(video.sigmas, build_sigma_schedule(7, VIDEO_SHIFT).sigmas, rtol=0, atol=0)
        torch.testing.assert_close(audio.sigmas, build_sigma_schedule(7, AUDIO_SHIFT).sigmas, rtol=0, atol=0)


# -- data_estimate / euler_step ------------------------------------------------

def test_data_estimate_matches_independent_reference():
    sample = torch.tensor([1.0, -2.0, 0.5])
    velocity = torch.tensor([0.3, 0.1, -0.4])
    timestep = 0.62
    got = data_estimate(velocity, timestep, sample)
    ref = sample + (1.0 - timestep) * velocity
    torch.testing.assert_close(got, ref, rtol=0, atol=1e-6)


def test_euler_step_matches_independent_reference_scalar_case():
    sample = torch.tensor(2.0)
    velocity = torch.tensor(-0.7)
    got = euler_step(velocity, 0.4, sample, sigma=0.6, sigma_next=0.3)
    ref = _ref_euler_step(velocity, 0.4, sample, 0.6, 0.3)
    torch.testing.assert_close(got, ref, rtol=0, atol=1e-6)


def test_bite_check_euler_step_sign_is_load_bearing():
    # BITE CHECK: negating the velocity in the INDEPENDENT reference must
    # produce a materially different result than schedule.euler_step -- if it
    # didn't, the comparisons above would be vacuously insensitive to the
    # data-ward-vs-noise-ward sign convention this module exists to get right.
    sample = torch.tensor(2.0)
    velocity = torch.tensor(0.9)
    got = euler_step(velocity, 0.4, sample, sigma=0.6, sigma_next=0.3)
    ref_correct = _ref_euler_step(velocity, 0.4, sample, 0.6, 0.3, negate_velocity=False)
    ref_wrong_sign = _ref_euler_step(velocity, 0.4, sample, 0.6, 0.3, negate_velocity=True)
    torch.testing.assert_close(got, ref_correct, rtol=0, atol=1e-6)
    assert abs(float(got) - float(ref_wrong_sign)) > 1e-3


def test_euler_step_uses_grid_sigma_not_recomputed_from_timestep():
    # dossier trap 3: the Euler ratio must use the schedule's OWN sigma
    # values, not `1 - timestep` recomputed -- feed a sigma that deliberately
    # disagrees with `1 - timestep` and confirm the result follows the
    # PASSED sigma, not a silently-recomputed one.
    sample = torch.tensor(1.0)
    velocity = torch.tensor(0.5)
    timestep = 0.4  # would recompute to sigma=0.6 if (wrongly) re-derived
    got = euler_step(velocity, timestep, sample, sigma=0.9, sigma_next=0.1)
    wrong = euler_step(velocity, timestep, sample, sigma=0.6, sigma_next=0.1)  # what a re-derive would give
    assert abs(float(got) - float(wrong)) > 1e-3


# -- full toy trajectory: video + audio streams, fixed fake model -----------

def _fake_velocity(x: torch.Tensor, t: float, *, kind: str) -> torch.Tensor:
    """A deterministic, non-trivial "model": mixes a per-row linear ramp with
    a t-dependent scale so video and audio see DIFFERENT outputs at the same
    step (never a constant-zero degenerate case)."""
    scale = 1.5 if kind == "video" else 0.4
    ramp = torch.arange(x.numel(), dtype=x.dtype).reshape(x.shape)
    return scale * (ramp - x) + 0.1 * t


def _run_trajectory(num_steps: int, *, build_schedule, step_fn, data_fn):
    video_shape = (4,)
    audio_shape = (3,)
    video_x = torch.linspace(-1.0, 1.0, 4)
    audio_x = torch.linspace(-0.5, 0.5, 3)
    video_sched = build_schedule(num_steps, VIDEO_SHIFT)
    audio_sched = build_schedule(num_steps, AUDIO_SHIFT)
    for i in range(video_sched[1].numel()):
        video_t = float(video_sched[1][i])
        audio_t = float(audio_sched[1][i])
        video_v = _fake_velocity(video_x, video_t, kind="video")
        audio_v = _fake_velocity(audio_x, audio_t, kind="audio")
        video_x = step_fn(video_v, video_t, video_x, video_sched[0][i], video_sched[0][i + 1])
        audio_x = step_fn(audio_v, audio_t, audio_x, audio_sched[0][i], audio_sched[0][i + 1])
    return video_x, audio_x


def test_toy_trajectory_reproduces_independent_reference_to_1e5():
    def module_schedule(steps, shift):
        s = build_sigma_schedule(steps, shift)
        return s.sigmas, s.timesteps

    def ref_schedule(steps, shift):
        return _ref_sigma_schedule(steps, shift)

    got_video, got_audio = _run_trajectory(6, build_schedule=module_schedule, step_fn=euler_step, data_fn=data_estimate)
    ref_video, ref_audio = _run_trajectory(6, build_schedule=ref_schedule, step_fn=_ref_euler_step, data_fn=None)

    torch.testing.assert_close(got_video, ref_video, rtol=1e-5, atol=1e-5)
    torch.testing.assert_close(got_audio, ref_audio, rtol=1e-5, atol=1e-5)
