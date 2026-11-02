from pathlib import Path

import pytest

import yaml

from scripts import recipe_lint
from src.features.recipes.catalog import plugin_recipe_roots
from src.features.presets.fixture_form import fixture_video_director_capabilities
from src.features.recipes.schema import parse_recipe
from src.platform.plugins.loader import PluginLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_RECIPES = REPO_ROOT / "content" / "recipes" / "marketplace"
PLUGINS = REPO_ROOT / "content" / "plugins" / "marketplace"

LONG_SMOKES = {
    "ltx25-starter": 600,
    "minimax-h3-fast-starter": 400,
    "minimax-h3-starter": 400,
    "wan22-starter": 600,
}

CLIP_LENGTH_FIELDS = {"frames", "duration", "num_frames"}


@pytest.fixture(scope="module")
def manifests():
    return PluginLoader(str(PLUGINS), str(REPO_ROOT / "content" / "plugins" / "_none")).discover_plugins()


@pytest.fixture(scope="module")
def preset_index(manifests):
    return recipe_lint.load_preset_index(plugin_manifests=manifests)


def _plugin_recipe_files(manifests):
    return sorted(f for root in plugin_recipe_roots(manifests) for f in Path(root.path).glob("*.yml"))


@pytest.fixture(scope="module")
def smokes(manifests):
    found = {}
    for path in sorted(CORE_RECIPES.glob("*.yml")) + _plugin_recipe_files(manifests):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("smoke"):
            recipe = parse_recipe(data, source_path=str(path))
            found[recipe.id] = recipe.smoke
    return found


def test_the_shipped_trees_are_not_empty(manifests):
    assert sorted(CORE_RECIPES.glob("*.yml"))
    assert _plugin_recipe_files(manifests)


def test_every_shipped_recipe_passes_the_recipe_lint(manifests, preset_index):
    extra_kinds = recipe_lint.plugin_step_kinds(manifests)
    files = sorted(CORE_RECIPES.glob("*.yml")) + _plugin_recipe_files(manifests)

    failures = {
        str(path.relative_to(REPO_ROOT)): issues
        for path in files
        if (issues := recipe_lint.lint_recipe_file(path, preset_index, extra_kinds))
    }

    assert failures == {}


@pytest.mark.parametrize(("recipe_id", "minimum"), sorted(LONG_SMOKES.items()))
def test_a_smoke_known_to_run_past_five_minutes_declares_enough_time(smokes, recipe_id, minimum):
    assert smokes[recipe_id].timeout_seconds >= minimum


def test_a_smoke_on_a_director_document_sets_no_clip_length_in_its_form(smokes, preset_index):
    director_smokes = {
        recipe_id
        for recipe_id, smoke in smokes.items()
        if fixture_video_director_capabilities(preset_index[smoke.preset_id], smoke.mode) is not None
    }
    assert {"minimax-h3-starter", "minimax-h3-fast-starter"} <= director_smokes

    dead_fields = {
        recipe_id: sorted(CLIP_LENGTH_FIELDS & set((smokes[recipe_id].form or {}).keys()))
        for recipe_id in director_smokes
    }

    assert {recipe_id: fields for recipe_id, fields in dead_fields.items() if fields} == {}
