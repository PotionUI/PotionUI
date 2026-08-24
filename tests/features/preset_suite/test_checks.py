"""Unit tests for the preset-suite sanity checks (synthetic PIL images, no GPU)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PIL import Image

from src.features.preset_suite.checks import (
    check_max_seconds,
    check_min_outputs,
    check_not_black,
    check_resolution,
    checks_passed,
    run_checks,
)
from src.features.preset_suite.models import CaseOutcome


def _img(arr):
    return Image.fromarray(arr.astype(np.uint8), "RGB")


def _black(w=64, h=64):
    return _img(np.zeros((h, w, 3)))


def _grey(w=64, h=64, v=128):
    return _img(np.full((h, w, 3), v))


def _gradient(w=64, h=64):
    row = np.linspace(0, 255, w)
    arr = np.stack([np.tile(row, (h, 1))] * 3, axis=-1)
    return _img(arr)


def _checks(**kw):
    base = {"min_outputs": 1, "resolution": None, "not_black": True, "max_seconds": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _outcome(images, seconds=None):
    return CaseOutcome(status="completed", images=images, seconds=seconds)


# --- not_black ---------------------------------------------------------------


def test_not_black_fails_on_all_black():
    r = check_not_black(_outcome([_black()]))
    assert r.passed is False
    assert "mean=" in r.detail


def test_not_black_passes_on_gradient():
    assert check_not_black(_outcome([_gradient()])).passed is True


def test_not_black_fails_if_any_image_black():
    assert check_not_black(_outcome([_gradient(), _black()])).passed is False


def test_not_black_flat_grey_is_black_by_low_std():
    # A perfectly flat mid-grey has std 0 -> flat/black by the std rule.
    assert check_not_black(_outcome([_grey(v=128)])).passed is False


def test_not_black_no_images_is_na_pass():
    assert check_not_black(_outcome([])).passed is True


# --- min_outputs -------------------------------------------------------------


def test_min_outputs_pass_and_fail():
    assert check_min_outputs(_outcome([_gradient()]), 1).passed is True
    assert check_min_outputs(_outcome([]), 1).passed is False
    assert check_min_outputs(_outcome([_gradient()]), 2).passed is False


# --- resolution --------------------------------------------------------------


def test_resolution_match_and_mismatch():
    assert check_resolution(_outcome([_gradient(64, 64)]), "64x64").passed is True
    r = check_resolution(_outcome([_gradient(64, 64)]), "128x128")
    assert r.passed is False and "mismatch" in r.detail


def test_resolution_malformed_is_failed_not_raised():
    r = check_resolution(_outcome([_gradient()]), "not-a-res")
    assert r.passed is False and "malformed" in r.detail


def test_resolution_no_images_is_na_pass():
    assert check_resolution(_outcome([]), "64x64").passed is True


# --- max_seconds -------------------------------------------------------------


def test_max_seconds_under_over_and_none():
    assert check_max_seconds(_outcome([], seconds=5.0), 10.0).passed is True
    assert check_max_seconds(_outcome([], seconds=15.0), 10.0).passed is False
    assert check_max_seconds(_outcome([], seconds=None), 10.0).passed is True  # n/a


# --- run_checks orchestration ------------------------------------------------


def test_run_checks_emits_only_applicable_checks():
    out = _outcome([_gradient()], seconds=3.0)
    results = run_checks(out, _checks(resolution="64x64", max_seconds=10.0))
    names = {r.name for r in results}
    assert names == {"min_outputs", "not_black", "resolution", "max_seconds"}
    assert checks_passed(results) is True


def test_run_checks_omits_disabled_and_unset():
    out = _outcome([_gradient()])
    results = run_checks(out, _checks(not_black=False))  # resolution/max_seconds unset
    names = {r.name for r in results}
    assert names == {"min_outputs"}


def test_run_checks_black_image_fails_aggregate():
    results = run_checks(_outcome([_black()]), _checks())
    assert checks_passed(results) is False


def test_checks_passed_empty_is_true():
    assert checks_passed([]) is True
