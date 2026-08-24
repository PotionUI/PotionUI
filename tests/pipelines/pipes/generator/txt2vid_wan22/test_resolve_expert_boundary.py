"""Unit tests for resolve_expert_boundary -- the shared Wan high/low-noise
expert switch-point resolver (picker sigma vs. 'switch at step' vs. spec
default). Pure CPU: build_sigmas is schedule math, no GPU involved."""

from src.pipelines.pipes.generator.txt2vid_wan22.main import resolve_expert_boundary
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


class _Spec:
    def __init__(self, sampling_settings):
        self.sampling_settings = sampling_settings


def test_spec_default_when_nothing_set():
    spec = _Spec({"expert_boundary": 0.875})
    assert resolve_expert_boundary(spec, {}, {}, 30) == 0.875


def test_empty_string_override_and_step_are_ignored():
    spec = _Spec({"expert_boundary": 0.900})
    got = resolve_expert_boundary(spec, {"expert_boundary": "", "expert_switch_step": ""}, {}, 30)
    assert got == 0.900


def test_numeric_boundary_override_wins_over_spec_default():
    spec = _Spec({"expert_boundary": 0.875})
    assert resolve_expert_boundary(spec, {"expert_boundary": "0.92"}, {}, 30) == 0.92


def test_switch_step_wins_over_boundary_and_maps_to_that_steps_sigma():
    spec = _Spec({"expert_boundary": 0.875, "shift": 5.0})
    sampling_settings = {"shift": 5.0}
    sigmas = build_sigmas(30, shift=5.0)
    got = resolve_expert_boundary(
        spec, {"expert_boundary": "0.5", "expert_switch_step": 10}, sampling_settings, 30
    )
    assert got == float(sigmas[10])


def test_switch_step_zero_falls_back_to_spec_default():
    spec = _Spec({"expert_boundary": 0.875})
    assert resolve_expert_boundary(spec, {"expert_switch_step": 0}, {}, 30) == 0.875


def test_switch_step_past_the_end_is_clamped_to_the_final_sigma():
    spec = _Spec({"expert_boundary": 0.875, "shift": 5.0})
    sigmas = build_sigmas(30, shift=5.0)
    got = resolve_expert_boundary(spec, {"expert_switch_step": 999}, {"shift": 5.0}, 30)
    assert got == float(sigmas[-1])  # the schedule's tail sigma (0.0): high expert throughout


def test_non_integer_switch_step_is_ignored_and_falls_through_to_boundary():
    spec = _Spec({"expert_boundary": 0.875})
    got = resolve_expert_boundary(
        spec, {"expert_switch_step": "abc", "expert_boundary": "0.9"}, {}, 30
    )
    assert got == 0.9


def test_step_conversion_uses_schedule_knobs_so_it_matches_the_run():
    # A schedule override (here: a different shift) must change the resolved
    # boundary -- proving the resolver reads the SAME sampling_settings the
    # denoise loop feeds build_sigmas, not a fixed schedule.
    spec = _Spec({"expert_boundary": 0.875, "shift": 5.0})
    low_shift = resolve_expert_boundary(spec, {"expert_switch_step": 8}, {"shift": 2.0}, 30)
    high_shift = resolve_expert_boundary(spec, {"expert_switch_step": 8}, {"shift": 8.0}, 30)
    assert low_shift != high_shift
