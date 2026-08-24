#!/usr/bin/env python3
"""
Preset lint budget gate - `release_gate.py` gate 5.

Two checks over the preset tree, both driven by `src.features.presets.linter.
PresetLinter` (the same linter `scripts/preset_lint.py` and
`GET /api/developer/presets/lint` use):

  1. HARD, non-negotiable: every preset referenced by any
     `content/recipes/{marketplace,local}/*.yml` file's `presets:` list
     (resolved via its `path_hint` under `content/presets/` - recipes only
     ever reference shipped presets; see `scripts/recipe_lint.py`'s
     `load_preset_index()` for the sibling convention this mirrors) must
     lint with ZERO errors. A starter-recipe
     preset with a lint error breaks onboarding directly, so no budget can
     excuse it - this check ignores the budget entirely.
  2. BUDGETED: the repo-wide preset-lint error count - across
     `content/presets/marketplace/` and `content/presets/local/`,
     mirroring `scripts/preset_lint.py`'s own default roots - must not exceed
     the numeric ceiling checked in at `tests/release/lint_budget.json`
     (`preset_lint_error_budget`). See that file's own `_comment` for what the
     number means and how to change it: short version, it's a burn-down
     ceiling meant to trend toward 0, not a permanent allowance to raise
     whenever it's inconvenient.

Usage:
    python tests/release/preset_lint_budget.py

Exit code is nonzero if either check fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.features.presets.linter import LintIssue, PresetLinter  # noqa: E402
from src.features.setup.recipe_schema import Recipe, parse_recipe, validate_recipe_dict  # noqa: E402

DEFAULT_PRESET_ROOTS = ("content/presets/marketplace", "content/presets/local")
BUDGET_FILE = Path(__file__).resolve().parent / "lint_budget.json"
DEFAULT_RECIPE_ROOTS = ("content/recipes/marketplace", "content/recipes/local")
PRESETS_ROOT = ROOT / "content" / "presets"


def load_budget(budget_file: Path = BUDGET_FILE) -> int:
    """Read `preset_lint_error_budget` from the checked-in budget file (see
    that file's own `_comment` key for what the number means). Raises
    `ValueError` with a clear message if the file is missing or malformed -
    a budget gate that silently defaults to "no limit" defeats its purpose."""
    if not budget_file.is_file():
        raise ValueError(f"{budget_file}: budget file not found")
    data = json.loads(budget_file.read_text())
    budget = data.get("preset_lint_error_budget")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError(
            f"{budget_file}: 'preset_lint_error_budget' must be a non-negative integer, got {budget!r}"
        )
    return budget


def budget_ok(error_count: int, budget: int) -> bool:
    """Pure pass/fail: the repo-wide preset-lint error count must not exceed
    the checked-in budget."""
    return error_count <= budget


def load_recipes(recipe_roots=DEFAULT_RECIPE_ROOTS) -> List[Recipe]:
    """Parse every `content/recipes/{marketplace,local}/*.yml` file into a
    `Recipe`. Recipe *shape* validity (schema_version, required fields,
    referential integrity) is already enforced by `scripts/recipe_lint.py` /
    release_gate.py gate 1 - this re-validates defensively so a malformed
    recipe fails loudly here too rather than crashing on an unchecked
    `parse_recipe` assumption."""
    recipes: List[Recipe] = []
    for root in recipe_roots:
        root_path = Path(root)
        if not root_path.is_absolute():
            root_path = ROOT / root_path
        if not root_path.exists():
            continue
        for recipe_file in sorted(root_path.glob("*.yml")):
            data = yaml.safe_load(recipe_file.read_text()) or {}
            issues = validate_recipe_dict(data)
            if issues:
                raise ValueError(f"{recipe_file}: invalid recipe, fix recipe-lint failures first: {issues}")
            recipes.append(parse_recipe(data, source_path=str(recipe_file)))
    return recipes


def resolve_recipe_preset_dirs(recipes: List[Recipe], presets_root: Path = PRESETS_ROOT) -> List[Path]:
    """Every preset directory a recipe references, resolved via `path_hint`
    under `presets_root` (recipes only ever reference shipped presets - see
    module docstring). Pure path arithmetic, no
    filesystem access - existence is checked by the caller / by
    `recipe_lint.py` separately."""
    dirs: List[Path] = []
    for recipe in recipes:
        for preset_ref in recipe.presets:
            if not preset_ref.path_hint:
                raise ValueError(
                    f"{recipe.source_path}: preset '{preset_ref.preset_id}' has no path_hint - "
                    f"cannot resolve which preset directory to lint"
                )
            dirs.append(presets_root / preset_ref.path_hint)
    return dirs


def _errors_under(issues: List[LintIssue], preset_dirs: List[Path]) -> List[LintIssue]:
    """Errors whose `preset_path` is a preset.yml directly under one of
    `preset_dirs` (recipe-referenced presets)."""
    wanted = {str(d / "preset.yml") for d in preset_dirs}
    return [i for i in issues if i.level == "error" and i.preset_path in wanted]


def main() -> int:
    try:
        budget = load_budget()
        recipes = load_recipes()
        recipe_preset_dirs = resolve_recipe_preset_dirs(recipes)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    linter = PresetLinter([str(ROOT / r) for r in DEFAULT_PRESET_ROOTS])
    issues = linter.lint()
    errors = [i for i in issues if i.level == "error"]

    ok = True

    hard_failures = _errors_under(issues, recipe_preset_dirs)
    if hard_failures:
        ok = False
        print("HARD FAILURE: recipe-referenced preset(s) have lint errors (no budget excuses this):")
        for issue in hard_failures:
            print(f"  {issue}")
    else:
        print(f"Recipe-referenced presets ({len(recipe_preset_dirs)}): 0 lint errors. OK.")

    print(f"\nRepo-wide preset lint: {len(errors)} error(s) (budget: {budget})")
    if not budget_ok(len(errors), budget):
        ok = False
        print(f"BUDGET EXCEEDED: {len(errors)} error(s) > budget {budget}")
        for issue in errors:
            print(f"  {issue}")
    else:
        print("Within budget.")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
