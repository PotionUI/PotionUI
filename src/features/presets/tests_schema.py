"""
Preset E2E test schema (pydantic v2): the ``tests.yml`` a preset may ship next
to its ``preset.yml`` to describe end-to-end test cases run through the real
generation pipeline (see ``docs/presets.md`` "Testing presets" for the full
authoring reference).

This module is the FROZEN contract the standalone runner
(``scripts/preset_test_suite.py``) is built against — it imports exactly the
names below:

- :func:`load_tests_yml` — ``(preset_dir: Path) -> PresetTests | None``
- :class:`PresetTests`, :class:`TestCase`, :class:`ModelRef`, :class:`HFRef`,
  :class:`Checks`

Design note on ``extra="forbid"`` (contrast with ``SpeedProfile`` in
``schema.py``, which is deliberately ``extra="allow"``): a preset test suite
exists purely to catch regressions before a human looks at a generated image.
A typo'd key here (``sha256`` misspelled as ``sha265``, ``min_outpts``) would
silently produce a case that "passes" without checking what it was supposed
to check — worse than not having the test at all, and much harder to notice
than a preset that fails to load. ``SpeedProfile``'s looseness exists so a
forward-compat knob doesn't break every preset that has a `speed_profiles:`
block; nothing here has that pressure, so failing loudly on a typo is the
better default.

sha256/HF convention (models: dict)
------------------------------------
Every ``TestCase`` needs concrete model weights to run against. Models are
referenced by **sha256**, not filename, so a test suite survives a checkpoint
being renamed/re-downloaded: ``ModelRef.sha256`` is the content identity the
runner resolves against the local model index (and, if ``hf:`` is given and
the file isn't found locally, fetches from the named Hugging Face repo/file
and verifies the hash after download). A case whose model(s) are not
available in this checkout yet should use the **placeholder sha256**
``"0" * 64`` (64 zero digits — a value no real ``sha256sum`` output can ever
produce) for every ``ModelRef`` it needs, and add the ``"needs-model"`` tag.
The runner is expected to skip (not fail) cases carrying that placeholder;
:mod:`src.features.presets.linter` also recognizes the convention (see
``_lint_tests_yml``) so a placeholder case is never mistaken for a real,
passing regression test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# All-zero sha256: no real file hashes to this. Marks a case's model(s) as not
# locally resolvable yet (see module docstring). Exported so the runner and
# the linter share one literal instead of each hardcoding "0" * 64.
PLACEHOLDER_SHA256 = "0" * 64
NEEDS_MODEL_TAG = "needs-model"

TESTS_SCHEMA_VERSION = 1


class TestsYmlError(ValueError):
    """Raised by :func:`load_tests_yml` when a preset's ``tests.yml`` exists
    but fails to parse or validate. The message always names the preset
    directory and, where the error can be attributed to one, the offending
    case's name (or index, if the case itself is malformed enough that even
    its name didn't parse) — a CI failure log should never require re-opening
    the file to find out what broke.
    """


def _format_tests_errors(prefix: str, raw_cases: Any, exc: ValidationError) -> List[str]:
    """Format a ``PresetTests`` ``ValidationError`` as
    ``'<prefix>: case <name-or-index>: <path>: <message>'``, resolving the
    case name from the raw (pre-validation) YAML when the error's location
    starts with ``cases[<idx>]`` so a broken case is identifiable even when
    the field that broke it isn't ``name`` itself.
    """
    formatted = []
    raw_case_list = raw_cases if isinstance(raw_cases, list) else []
    for err in exc.errors():
        loc = err["loc"]
        if loc and loc[0] == "cases" and len(loc) > 1 and isinstance(loc[1], int):
            idx = loc[1]
            case_label = f"index {idx}"
            if idx < len(raw_case_list) and isinstance(raw_case_list[idx], dict):
                name = raw_case_list[idx].get("name")
                if name:
                    case_label = f"'{name}'"
            sub_loc = ".".join(str(p) for p in loc[2:]) or "<case>"
            formatted.append(f"{prefix}: case {case_label}: {sub_loc}: {err['msg']}")
        else:
            loc_str = ".".join(str(p) for p in loc) or "<root>"
            formatted.append(f"{prefix}: {loc_str}: {err['msg']}")
    return formatted


# ---------------------------------------------------------------------------
# Model references
# ---------------------------------------------------------------------------


class HFRef(BaseModel):
    """Optional Hugging Face fetch fallback for a :class:`ModelRef` whose
    ``sha256`` isn't found in the local model index."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    file: str


class ModelRef(BaseModel):
    """One entry in a :class:`TestCase`'s ``models:`` dict. The dict key is
    the form-field name (e.g. ``diffusion_model``, ``vae``, ``model``) the
    resolved local path is injected into before submission — see
    ``docs/presets.md``."""

    model_config = ConfigDict(extra="forbid")

    sha256: str
    hf: Optional[HFRef] = None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class Checks(BaseModel):
    """Post-generation assertions the runner applies to a case's output(s).
    All optional/defaulted — a case with no ``checks:`` block still gets the
    baseline ``min_outputs``/``not_black`` sanity checks."""

    model_config = ConfigDict(extra="forbid")

    min_outputs: int = 1
    # "WxH" (all outputs must match) or a list of "WxH" (each output must
    # match ONE of the listed sizes, order-independent) — the latter covers a
    # case whose outputs legitimately differ in size (e.g. a batch that
    # includes a hires-pass output alongside the base-resolution ones). A
    # bare string is still the common case and needs no list wrapping.
    resolution: Optional[Union[str, List[str]]] = None
    not_black: bool = True
    max_seconds: Optional[float] = None


# ---------------------------------------------------------------------------
# Test case
# ---------------------------------------------------------------------------


class TestCase(BaseModel):
    """One end-to-end generation to run through the real pipeline.

    ``name`` should be kebab-case and unique within the file (enforced by the
    linter's cross-case check, not this schema — see
    ``PresetLinter._lint_tests_yml``, which needs the whole list at once to
    detect a duplicate; a single ``TestCase`` can't see its siblings).

    ``form`` holds partial form values — anything not set here falls back to
    the preset form's own defaults. Two conventions the runner follows
    (documented in full in ``docs/presets.md``): a ``prompt`` key inside
    ``form`` maps to the generation request's top-level prompt (it is not a
    preset form field), and ``seed`` must NOT be duplicated inside ``form`` —
    this ``TestCase.seed`` is what the runner uses for determinism, and it
    always wins.

    ``models`` maps a form-field name to a :class:`ModelRef`; the runner
    resolves each by sha256 and injects the resolved local path into ``form``
    before submission (so ``form`` itself never hardcodes a filename/path).

    ``kind`` hints what the case's outputs are, so the runner can pick the
    right check semantics: ``"image"`` (default) uses ``checks.min_outputs``/
    ``not_black`` against generated images as they work today. ``"video"``
    exists so a video-preset case's ``not_black`` doesn't vacuously pass on
    zero image outputs — the runner is expected to grow video-shaped checks
    (e.g. frame count in place of ``min_outputs``) keyed off this field.
    Declaring it does not change behavior for ``"image"`` cases; the schema
    only records the hint; the check *semantics* live in the runner.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    mode: str
    kind: Literal["image", "video"] = "image"
    form: Dict[str, Any] = Field(default_factory=dict)
    seed: int
    tags: List[str] = Field(default_factory=lambda: ["fast"])
    models: Dict[str, ModelRef] = Field(default_factory=dict)
    checks: Checks = Field(default_factory=Checks)


class PresetTests(BaseModel):
    """Top-level shape of a preset's ``tests.yml``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(..., alias="schema")
    cases: List[TestCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_schema_version(self) -> "PresetTests":
        if self.schema_version != TESTS_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported tests.yml schema version: {self.schema_version}. "
                f"Only {TESTS_SCHEMA_VERSION} is supported."
            )
        return self


# ---------------------------------------------------------------------------
# Validation / loading entry points
# ---------------------------------------------------------------------------


def validate_tests_yml(data: dict, prefix: str = "tests.yml") -> Tuple[Optional[PresetTests], List[str]]:
    """Validate already-parsed ``tests.yml`` YAML data. Never raises — mirrors
    ``schema.py``'s ``validate_manifest``/etc. so callers (the linter) collect
    every error for a file instead of stopping at the first one."""
    try:
        return PresetTests.model_validate(data), []
    except ValidationError as exc:
        return None, _format_tests_errors(prefix, data.get("cases"), exc)


def load_tests_yml(preset_dir: Path) -> Optional[PresetTests]:
    """Load and validate ``<preset_dir>/tests.yml``.

    Returns ``None`` when the preset ships no ``tests.yml`` at all (not an
    error — most presets won't have one yet). Raises :class:`TestsYmlError`
    when the file exists but fails to parse as YAML or fails schema
    validation, with a message naming the preset directory and (where
    resolvable) the offending case.
    """
    tests_file = preset_dir / "tests.yml"
    if not tests_file.exists():
        return None

    prefix = str(tests_file)
    try:
        with open(tests_file, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        raise TestsYmlError(f"{prefix}: failed to parse YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise TestsYmlError(f"{prefix}: top level must be a mapping (schema:, cases:), got {type(data).__name__}")

    model, errors = validate_tests_yml(data, prefix)
    if errors:
        raise TestsYmlError("; ".join(errors))
    return model
