#!/usr/bin/env python3
"""
Release smoke gate — orchestrates the checks a release must pass
before it ships, and prints one clear pass/fail summary at the end.

    python tests/release/release_gate.py               # run everything (GPU
                                                         # gate auto-skips if
                                                         # no CUDA)
    python tests/release/release_gate.py --skip-gpu     # force-skip the GPU
                                                         # gate, useful for
                                                         # local iteration on
                                                         # a CUDA machine

Gates, in order:
  1. Recipe lint          — `python scripts/recipe_lint.py` over `recipes/`.
  2. Architecture layering — `pytest tests/architecture/` (+
     `tests/scripts/test_constraints_cover_requirements.py` if present),
     matching the `layering` job in `.github/workflows/onboarding-smoke.yml`.
  3. Setup feature suite   — `pytest tests/features/setup/`
     (setup-run/recipe test tree).
  4. GPU-gated preset E2E  — `python scripts/preset_test_suite.py
     --preset native/SDXL --tag fast`, the starter-recipe preset
     `recipes/sdxl-starter.yml` points at. Only runs when a CUDA device is
     actually detected (or unconditionally skipped via --skip-gpu); a skip is
     NOT a failure, but it is printed loudly so it's never mistaken for
     silent success.
  5. Preset lint budget   — `python tests/release/preset_lint_budget.py`.
     Every preset a `recipes/*.yml` file references must lint with zero
     errors (hard, no exceptions), and the repo-wide preset-lint error count
     must stay within the checked-in ceiling at `tests/release/lint_budget.json`.
     CPU-only, no CUDA gating needed.

This script is pure subprocess orchestration: it never imports `api.py` or
`src.*` itself (each gate is a separate `python -m ...`/`python ...`
subprocess with its own interpreter and import graph), and it never starts
the API server or a real GPU generation outside of gate 4's own explicit,
CUDA-gated preset run.

Exit code is nonzero iff any gate that actually ran (i.e. not SKIPPED)
failed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _subprocess_env() -> dict:
    """Copy the current environment and prepend the venv site-packages +
    repo root to PYTHONPATH — the CLAUDE.md convention for this container/CI,
    where the venv does not activate cleanly."""
    env = os.environ.copy()
    venv_site_packages = str(ROOT / "venv" / "lib" / "python3.12" / "site-packages")
    existing = env.get("PYTHONPATH", "")
    parts = [venv_site_packages, str(ROOT)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _run(cmd: List[str], *, label: str) -> bool:
    """Run a subprocess, streaming its output live. Returns True on exit 0."""
    print(f"\n{'=' * 72}")
    print(f"[{label}] $ {' '.join(cmd)}")
    print("=" * 72)
    result = subprocess.run(cmd, cwd=str(ROOT), env=_subprocess_env())
    ok = result.returncode == 0
    print(f"\n[{label}] exit code {result.returncode} -> {'PASS' if ok else 'FAIL'}")
    return ok


def gate_recipe_lint() -> bool:
    """(a) Recipe lint over content/recipes/. recipe_lint.py already defaults
    to linting `content/recipes/marketplace` + `content/recipes/local` with no
    arguments (see its `main`)."""
    return _run([sys.executable, "scripts/recipe_lint.py"], label="recipe-lint")


def gate_layering() -> bool:
    """(b) The architecture layering guard, matching the `layering` job in
    .github/workflows/onboarding-smoke.yml. That job also runs
    tests/scripts/test_constraints_cover_requirements.py alongside — include
    it here too, but only if it actually exists in this checkout."""
    targets = ["tests/architecture/"]
    constraints_test = ROOT / "tests" / "scripts" / "test_constraints_cover_requirements.py"
    if constraints_test.is_file():
        targets.append("tests/scripts/test_constraints_cover_requirements.py")
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "--no-cov"]
    return _run(cmd, label="architecture-layering")


def gate_setup_suite() -> bool:
    """(c) The setup feature test suite — setup-run/recipe test tree."""
    cmd = [sys.executable, "-m", "pytest", "tests/features/setup/", "-q", "--no-cov"]
    return _run(cmd, label="setup-suite")


def _cuda_available() -> bool:
    """Subprocess-safe CUDA probe: spawn a throwaway interpreter so a torch
    import (or its absence) can never crash *this* process, and so we don't
    pay torch's import cost unless something downstream actually needs it."""
    probe = "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(ROOT),
            env=_subprocess_env(),
            capture_output=True,
            timeout=120,
        )
        return result.returncode == 0
    except Exception:
        # torch not importable, no interpreter, timeout, etc. — treat exactly
        # like "no GPU" rather than raising out of the gate.
        return False


def gate_gpu_preset_e2e(skip_gpu: bool) -> str:
    """(d) GPU-gated preset E2E coverage.

    `recipes/sdxl-starter.yml`'s `presets[0].preset_id` /
    `smoke.preset_id` (`01K0W24A3RADXXABH16YQ7KE90`) matches
    `content/presets/marketplace/SDXL/preset.yml`'s `id:` — SDXL native is the
    starter-recipe preset. `content/presets/marketplace/SDXL/tests.yml` has cases
    tagged "fast" (e.g. `sdxl-cyberrealistic-pony-baseline-fast`), so
    `--tag fast` is used to keep this to the cheapest real case rather than
    the whole suite.

    Returns one of PASS / FAIL / SKIP (never raises for the "no GPU" case).
    """
    if skip_gpu:
        print("\nGPU E2E preset coverage SKIPPED — --skip-gpu was passed.")
        return SKIP

    if not _cuda_available():
        print(
            "\nGPU E2E preset coverage SKIPPED — no CUDA device on this host "
            "(expected on CI)."
        )
        return SKIP

    cmd = [
        sys.executable,
        "scripts/preset_test_suite.py",
        "--preset",
        "native/SDXL",
        "--tag",
        "fast",
    ]
    ok = _run(cmd, label="gpu-preset-e2e")
    return PASS if ok else FAIL


def gate_preset_lint_budget() -> bool:
    """(e) Preset lint budget — `python tests/release/preset_lint_budget.py`.

    Two checks, both described in that script's own docstring and in
    `tests/release/lint_budget.json`'s `_comment`: every preset a `recipes/*.yml`
    file references (via `path_hint`) must lint with zero errors — hard, no
    budget applies — and the repo-wide preset-lint error count must stay
    within the checked-in `preset_lint_error_budget` (a burn-down ceiling,
    not a permanent allowance). CPU-only: the underlying `PresetLinter` is
    pure filesystem + YAML, so this needs no CUDA gating.
    """
    preset_lint_budget = Path(__file__).resolve().parent / "preset_lint_budget.py"
    return _run([sys.executable, str(preset_lint_budget)], label="preset-lint-budget")


def _print_summary(results: List[tuple]) -> bool:
    """results: list of (gate_name, status). Returns overall pass/fail."""
    print(f"\n{'=' * 72}")
    print("RELEASE GATE SUMMARY")
    print("=" * 72)
    name_width = max(len(name) for name, _ in results) + 2
    for name, status in results:
        print(f"  {name:<{name_width}} {status}")

    overall_ok = all(status != FAIL for _, status in results)
    print("=" * 72)
    if overall_ok:
        print("OVERALL: PASS (all gates passed or were explicitly skipped)")
    else:
        failed = [name for name, status in results if status == FAIL]
        print(f"OVERALL: FAIL ({', '.join(failed)})")
    print("=" * 72)
    return overall_ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release smoke gate: recipe lint, architecture layering, "
        "setup suite, (CUDA-gated) preset E2E coverage, and preset lint budget.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-gpu",
        action="store_true",
        help="Force-skip the GPU-gated preset E2E gate, even if a CUDA "
        "device is detected. Useful for fast local iteration.",
    )
    args = parser.parse_args(argv)

    results = []

    results.append(("recipe-lint", PASS if gate_recipe_lint() else FAIL))
    results.append(("architecture-layering", PASS if gate_layering() else FAIL))
    results.append(("setup-suite", PASS if gate_setup_suite() else FAIL))
    results.append(("gpu-preset-e2e", gate_gpu_preset_e2e(args.skip_gpu)))
    results.append(("preset-lint-budget", PASS if gate_preset_lint_budget() else FAIL))

    overall_ok = _print_summary(results)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
