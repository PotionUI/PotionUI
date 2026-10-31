import yaml

from scripts import recipe_new
from src.features.recipes.lint import lint_recipe
from src.features.recipes.schema import parse_recipe, validate_recipe_dict
from tests.features.recipes.mode_fixtures import video_preset


def test_scaffold_is_a_valid_mode_aware_starter_that_lints_clean():
    preset = video_preset()
    data = yaml.safe_load(recipe_new.render(preset, "video", ["huggingface-provider"], ["audio_vae"]))

    assert validate_recipe_dict(data, extra_kinds={"workspace.activate"}) == []
    plan = next(s for s in data["steps"] if s["kind"] == "modes.plan")
    assert plan["params"] == {
        "preset_id": "VID", "modes": "all", "selected": ["video"], "locked": ["video"],
        "extras": "offer", "preselect_extras": ["audio_vae"],
    }
    assert data["id"] == "vid-1-0-starter"
    assert data["artifacts"] == []
    errors, warnings = lint_recipe(parse_recipe(data), {"VID": preset})
    assert errors == []
    assert warnings == []


def test_find_preset_by_id_name_or_folder():
    preset = video_preset()
    assert recipe_new.find_preset("VID", [preset]) is preset
    assert recipe_new.find_preset("vid-1.0", [preset]) is preset
    assert recipe_new.find_preset("nope", [preset]) is None
