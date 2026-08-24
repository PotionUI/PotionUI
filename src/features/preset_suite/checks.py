"""Sanity checks for the preset E2E test suite.

Turns a :class:`~src.features.preset_suite.models.CaseOutcome` (what a generation
produced) plus the case's declared ``Checks`` (from
``src.features.presets.tests_schema``) into a list of :class:`CheckResult`s. The
``Checks`` object is duck-typed — every field is read via ``getattr`` with the
schema default — so the checks keep working even if the schema class shifts
slightly. Image checks only for now (video/audio outputs are out of scope).
"""

from __future__ import annotations

from typing import Any, List

import numpy as np

from src.features.preset_suite.models import CaseOutcome, CheckResult


# not_black thresholds on the 0-255 uint8 scale: an image whose mean brightness
# is below this, OR whose std (contrast) is below this, reads as black/flat.
_BLACK_MEAN_MAX = 2.0
_BLACK_STD_MIN = 1.0


def _as_uint8_rgb(image: Any) -> np.ndarray:
    """Decode a PIL image to an (H, W, 3) uint8 array."""
    arr = np.asarray(image.convert("RGB"))
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    return arr


def _image_size(image: Any) -> tuple:
    """(width, height) of a PIL image."""
    return tuple(image.size)  # PIL .size is (width, height)


def check_min_outputs(outcome: CaseOutcome, minimum: int) -> CheckResult:
    n = len(outcome.images)
    ok = n >= minimum
    return CheckResult(
        name="min_outputs",
        passed=ok,
        detail=f"{n} output image(s), need >= {minimum}",
    )


def check_not_black(outcome: CaseOutcome) -> CheckResult:
    """Fail if ANY output image is black/flat (mean < 2 or std < 1 on uint8).

    Not evaluated when there are no images — ``min_outputs`` already covers the
    empty case, and there's nothing to judge here.
    """
    if not outcome.images:
        return CheckResult(name="not_black", passed=True, detail="no images to evaluate (n/a)")

    # Score each image once; the "worst" (a black one if any, else the
    # darkest/flattest) drives the reported detail.
    stats = []  # (is_black, mean+std, mean, std, index)
    for i, image in enumerate(outcome.images):
        arr = _as_uint8_rgb(image)
        mean = float(arr.mean())
        std = float(arr.std())
        is_black = mean < _BLACK_MEAN_MAX or std < _BLACK_STD_MIN
        stats.append((is_black, mean + std, mean, std, i))

    # A black image outranks a non-black one; among ties, lower (mean+std) is worse.
    _, _, w_mean, w_std, w_idx = max(stats, key=lambda s: (s[0], -s[1]))
    any_black = any(s[0] for s in stats)
    return CheckResult(
        name="not_black",
        passed=not any_black,
        detail=(
            f"worst image #{w_idx}: mean={w_mean:.2f}, std={w_std:.2f} "
            f"(black if mean<{_BLACK_MEAN_MAX} or std<{_BLACK_STD_MIN})"
        ),
    )


def _parse_resolution(spec: str) -> tuple:
    """Parse a 'WxH' string to (width, height); raises ValueError if malformed."""
    parts = str(spec).lower().replace(" ", "").split("x")
    if len(parts) != 2:
        raise ValueError(f"expected 'WxH', got {spec!r}")
    return int(parts[0]), int(parts[1])


def check_resolution(outcome: CaseOutcome, spec: str) -> CheckResult:
    try:
        want = _parse_resolution(spec)
    except (ValueError, TypeError):
        return CheckResult(
            name="resolution",
            passed=False,
            detail=f"malformed resolution {spec!r} (expected 'WxH' like '1024x1024')",
        )

    mismatches = []
    for i, image in enumerate(outcome.images):
        size = _image_size(image)
        if size != want:
            mismatches.append(f"#{i}={size[0]}x{size[1]}")

    if not outcome.images:
        return CheckResult(name="resolution", passed=True, detail="no images to evaluate (n/a)")

    ok = not mismatches
    detail = f"want {want[0]}x{want[1]}"
    if mismatches:
        detail += f"; mismatches: {', '.join(mismatches)}"
    return CheckResult(name="resolution", passed=ok, detail=detail)


def check_max_seconds(outcome: CaseOutcome, budget: float) -> CheckResult:
    if outcome.seconds is None:
        return CheckResult(name="max_seconds", passed=True, detail="no timing captured (n/a)")
    ok = outcome.seconds <= budget
    return CheckResult(
        name="max_seconds",
        passed=ok,
        detail=f"{outcome.seconds:.2f}s vs budget {budget:.2f}s",
    )


def run_checks(outcome: CaseOutcome, checks: Any) -> List[CheckResult]:
    """Evaluate every declared check against ``outcome``; one CheckResult each.

    ``checks`` is the case's ``Checks`` object (duck-typed). Checks that don't
    apply (``not_black`` disabled, ``resolution``/``max_seconds`` unset) are
    simply omitted from the result list.
    """
    results: List[CheckResult] = []

    minimum = getattr(checks, "min_outputs", 1)
    if minimum is None:
        minimum = 1
    results.append(check_min_outputs(outcome, int(minimum)))

    if getattr(checks, "not_black", True):
        results.append(check_not_black(outcome))

    resolution = getattr(checks, "resolution", None)
    if resolution:
        results.append(check_resolution(outcome, resolution))

    max_seconds = getattr(checks, "max_seconds", None)
    if max_seconds is not None:
        results.append(check_max_seconds(outcome, float(max_seconds)))

    return results


def checks_passed(results: List[CheckResult]) -> bool:
    """True iff every check passed (an empty list vacuously passes)."""
    return all(r.passed for r in results)
