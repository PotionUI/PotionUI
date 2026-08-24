"""Native engine error classes: message shape, forensic attribute wiring."""

from __future__ import annotations

from src.platform.runtime.native.errors import (
    PoisonedConditioningError,
    SamplingNumericsError,
)


def test_sampling_numerics_error_default_message_unchanged():
    err = SamplingNumericsError(4, "euler", "sage2")
    assert str(err) == "numerical instability (NaN/Inf) at step 4 (sampler=euler, attention=sage2)"
    assert err.tensor_name is None
    assert err.switch_step is None


def test_sampling_numerics_error_tensor_name_and_switch_step_in_message():
    err = SamplingNumericsError(3, "unipc", "sage2", tensor_name="x0", switch_step=2)
    msg = str(err)
    assert "tensor=x0" in msg
    assert "expert=low, switch_step=2" in msg


def test_sampling_numerics_error_switch_step_active_expert_before_switch():
    err = SamplingNumericsError(1, "unipc", switch_step=2)
    assert "expert=high" in str(err)


def test_annotate_segment_appends_to_message_and_sets_attributes():
    err = SamplingNumericsError(3, "unipc", "sage2", tensor_name="x0", switch_step=2)
    err.annotate_segment(1, "chain/i2v")
    assert err.segment_index == 1
    assert err.segment_label == "chain/i2v"
    assert str(err).endswith("segment 1 (chain/i2v)")


def test_annotate_segment_without_label():
    err = SamplingNumericsError(0, "euler")
    err.annotate_segment(2)
    assert str(err).endswith("segment 2")
    assert err.segment_label is None


def test_poisoned_conditioning_error_message():
    err = PoisonedConditioningError("cond", "context")
    assert "cond" in str(err)
    assert "context" in str(err)
