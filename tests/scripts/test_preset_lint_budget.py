"""Tests for tests/release/preset_lint_budget.py (release-gate 5).

Covers the pure/near-pure helpers in isolation rather than shelling out to
the full gate: `budget_ok` (the pass/fail arithmetic), `resolve_recipe_preset_dirs`
(path_hint resolution against fixture Recipe objects, no filesystem I/O), and
`load_budget` (reads the real checked-in `tests/release/lint_budget.json`).
"""

import sys
from pathlib import Path

import pytest

# `tests/scripts/` has no __init__.py on purpose - a dotted `tests.release.*`
# import can silently resolve to the third-party `tests` package ultralytics
# ships in site-packages, which PYTHONPATH puts ahead of the repo root. Add
# the release dir straight to sys.path and import it unqualified instead.
_RELEASE_DIR = Path(__file__).resolve().parents[1] / "release"
if str(_RELEASE_DIR) not in sys.path:
    sys.path.insert(0, str(_RELEASE_DIR))

from preset_lint_budget import budget_ok, load_budget, resolve_recipe_preset_dirs  # noqa: E402
from src.features.setup.recipe_schema import Recipe, RecipePresetRef


def _recipe(presets, source_path="content/recipes/marketplace/fake.yml") -> Recipe:
    return Recipe(
        id="fake",
        schema_version=1,
        version=1,
        name="Fake",
        engine="native",
        presets=presets,
        source_path=source_path,
    )


class TestBudgetOk:
    def test_under_budget_passes(self):
        assert budget_ok(0, 5) is True

    def test_exactly_at_budget_passes(self):
        assert budget_ok(3, 3) is True

    def test_over_budget_fails(self):
        assert budget_ok(4, 3) is False

    def test_zero_budget_zero_errors_passes(self):
        assert budget_ok(0, 0) is True


class TestResolveRecipePresetDirs:
    def test_single_recipe_single_preset(self, tmp_path):
        presets_root = tmp_path / "presets"
        recipe = _recipe([RecipePresetRef(preset_id="p1", path_hint="native/SDXL")])
        dirs = resolve_recipe_preset_dirs([recipe], presets_root=presets_root)
        assert dirs == [presets_root / "native" / "SDXL"]

    def test_multiple_recipes_multiple_presets(self, tmp_path):
        presets_root = tmp_path / "presets"
        recipe_a = _recipe(
            [RecipePresetRef(preset_id="p1", path_hint="native/SDXL")],
            source_path="content/recipes/marketplace/a.yml",
        )
        recipe_b = _recipe(
            [
                RecipePresetRef(preset_id="p2", path_hint="comfyui/SDXL"),
                RecipePresetRef(preset_id="p3", path_hint="native/Flux"),
            ],
            source_path="content/recipes/marketplace/b.yml",
        )
        dirs = resolve_recipe_preset_dirs([recipe_a, recipe_b], presets_root=presets_root)
        assert dirs == [
            presets_root / "native" / "SDXL",
            presets_root / "comfyui" / "SDXL",
            presets_root / "native" / "Flux",
        ]

    def test_missing_path_hint_raises(self, tmp_path):
        presets_root = tmp_path / "presets"
        recipe = _recipe([RecipePresetRef(preset_id="p1", path_hint="")])
        with pytest.raises(ValueError, match="path_hint"):
            resolve_recipe_preset_dirs([recipe], presets_root=presets_root)


class TestLoadBudget:
    def test_reads_real_checked_in_budget_file(self):
        budget = load_budget()
        assert isinstance(budget, int)
        assert budget >= 0

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found"):
            load_budget(tmp_path / "does-not-exist.json")

    def test_malformed_budget_value_raises(self, tmp_path):
        budget_file = tmp_path / "lint_budget.json"
        budget_file.write_text('{"preset_lint_error_budget": "not-a-number"}')
        with pytest.raises(ValueError, match="non-negative integer"):
            load_budget(budget_file)

    def test_negative_budget_raises(self, tmp_path):
        budget_file = tmp_path / "lint_budget.json"
        budget_file.write_text('{"preset_lint_error_budget": -1}')
        with pytest.raises(ValueError, match="non-negative integer"):
            load_budget(budget_file)
