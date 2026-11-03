import pytest

from src.features.presets.loader import PresetTemplateLoader
from src.features.recipes.executors._fixture_form import build_fixture_form_data

WAN_PRESET_ID = "01KX47WANVIDEO0000000000TV"


@pytest.fixture(scope="module")
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


def _form(loader, preset_id, overrides):
    return build_fixture_form_data(loader.load_preset_by_id(preset_id), "video", overrides=overrides)


def test_a_fast_speed_profile_sets_the_steps_and_cfg_its_reactions_declare(loader):
    form = _form(loader, WAN_PRESET_ID, {"speed_profile": "fast"})

    assert form["steps"] == 6
    assert form["cfg"] == 1.0


def test_the_default_speed_profile_keeps_the_static_defaults(loader):
    form = _form(loader, WAN_PRESET_ID, {})

    assert form["speed_profile"] == "balanced"
    assert form["steps"] == 30
    assert form["cfg"] == 5.0


def test_an_explicit_smoke_value_wins_over_the_reaction(loader):
    form = _form(loader, WAN_PRESET_ID, {"speed_profile": "fast", "steps": 12})

    assert form["steps"] == 12
    assert form["cfg"] == 1.0
