"""Shared result types for the preset E2E test suite.

These are the internal contract between the runner (produces a
:class:`CaseOutcome` per test case), the checks (turn an outcome + the case's
declared :class:`~src.features.presets.tests_schema.Checks` into
:class:`CheckResult`s), and the reporter (renders :class:`CaseResult`s to disk +
an HTML gallery). Kept free of any generation/torch import so every consumer is
unit-testable without a GPU.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


# Verdicts.
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass
class CaseOutcome:
    """What actually happened when the runner executed one test case.

    ``images`` are decoded PIL images captured from the generation's outputs
    (empty on failure/skip). ``status`` mirrors the generation's terminal state
    for a real run (``"completed"``/``"failed"``) or ``"skipped"`` when the case
    never ran (e.g. a model couldn't be resolved without ``--allow-download``).
    """

    status: str                                  # "completed" | "failed" | "skipped"
    images: List[Any] = field(default_factory=list)   # list[PIL.Image.Image]
    error: Optional[str] = None                  # generation/watchdog error text on failure
    seconds: Optional[float] = None              # wall-clock duration of the run
    submitted_form: dict = field(default_factory=dict)  # the form actually submitted (no prompt keys)
    skip_reason: Optional[str] = None            # why the case was skipped (never ran)
    # Prompt lifted OUT of the form onto the GenerationRequest (form.prompt /
    # form.negative_prompt aren't preset form fields — see the runner docstring).
    # Kept here so the report can show what was actually generated.
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None


@dataclass
class CheckResult:
    """One sanity check's verdict for a case."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    """A test case's full result: what ran, which checks passed, the verdict."""

    preset_id: str
    case_name: str
    verdict: str                                 # PASS | FAIL | SKIP
    outcome: CaseOutcome
    checks: List[CheckResult] = field(default_factory=list)
    reason: str = ""                             # one-line summary (fail/skip reason)
    tags: List[str] = field(default_factory=list)
    seed: Optional[int] = None
    mode: Optional[str] = None
    # Filled in by the reporter once images are written to the run directory
    # (relative paths from the run root, for the gallery to link).
    image_paths: List[str] = field(default_factory=list)
