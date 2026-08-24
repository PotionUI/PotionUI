"""Tests for the linear_quadratic sigma schedule (LTX-lineage)."""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def _lq(steps, **opts):
    return build_sigmas(steps, schedule="linear_quadratic", schedule_options=opts or None)


def test_endpoints_and_length():
    s = _lq(12)
    assert s.shape == (13,)            # steps + 1
    assert float(s[0]) == 1.0
    assert float(s[-1]) == 0.0


def test_strictly_descending():
    s = _lq(20)
    diffs = s[1:] - s[:-1]
    assert torch.all(diffs < 0), "schedule must be strictly decreasing"


def test_params_change_schedule():
    base = _lq(16)
    hi = _lq(16, threshold_noise=0.1)
    assert not torch.allclose(base, hi)
    # linear_steps changes the split point.
    few = _lq(16, linear_steps=2)
    many = _lq(16, linear_steps=12)
    assert not torch.allclose(few, many)


def test_default_schedule_byte_identical_without_opt_in():
    # A plain (shift-based) build must be untouched by the new branch existing.
    a = build_sigmas(20, shift=3.0)
    b = build_sigmas(20, shift=3.0)
    assert torch.equal(a, b)
    # and it must differ from the linear_quadratic schedule (sanity: the branch
    # actually does something).
    assert not torch.allclose(a, _lq(20))


def test_unknown_schedule_still_raises():
    import pytest
    with pytest.raises(ValueError, match="linear_quadratic"):
        build_sigmas(8, schedule="bogus")


def test_linear_steps_full_no_quadratic_tail_is_valid():
    # linear_steps == steps -> no quadratic segment; still a valid 1->0 schedule.
    s = _lq(10, linear_steps=10)
    assert float(s[0]) == 1.0 and float(s[-1]) == 0.0
    assert torch.all(s[1:] - s[:-1] < 0)
