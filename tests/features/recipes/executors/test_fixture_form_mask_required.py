import pytest

from src.features.forms.binding import FormBindingError
from src.features.presets.loader import PresetTemplateLoader
from src.features.recipes.executors._fixture_form import FIXTURE_TAG, build_fixture_form_data


@pytest.fixture(scope="module")
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


def _all_cases(loader):
    return [(preset, mode) for preset in loader.presets for mode in preset.modes]


def _find(loader, fragment, mode):
    for preset in loader.presets:
        if fragment.lower() in preset.name.lower() and mode in preset.modes:
            return preset, mode
    pytest.fail(f"no preset matching {fragment} with mode {mode}")


@pytest.mark.parametrize("fragment,mode", [("Z-Image", "inpaint"), ("MOSS", "inpaint")])
def test_mask_required_inpaint_modes_bind_with_a_fixture_mask(loader, fragment, mode):
    preset, mode = _find(loader, fragment, mode)

    form = build_fixture_form_data(preset, mode)

    assert form["source_image"]
    assert form["source_image_inpaint_mask"].startswith(f"/{FIXTURE_TAG}/media/")


def test_every_shipped_mode_passes_the_fixture_bind(loader):
    failures = []
    for preset, mode in _all_cases(loader):
        try:
            build_fixture_form_data(preset, mode)
        except FormBindingError as error:
            failures.append((preset.name, mode, str(error)))
    assert failures == []
