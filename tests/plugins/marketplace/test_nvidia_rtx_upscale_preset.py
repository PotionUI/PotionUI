from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESETS = Path(__file__).resolve().parents[3] / "content" / "plugins" / "marketplace" / "nvidia-rtx-upscale" / "presets"
PRESET_ID = "01KXRTXUPSCALE0000000000RU"
SOURCES = {"upscale": {"input_image": "/media/source.png"}, "video_upscale": {"input_video": "/media/source.mp4"}}


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader([str(PRESETS)])
    loader.load_presets()
    found = next((preset for preset in loader.presets if preset.id == PRESET_ID), None)
    assert found is not None, sorted(preset.id for preset in loader.presets)
    return found


def _pipe_config(template, mode, pipe_name, form):
    bound = bind_form(template, mode, form_name=None, raw_form_data=form, user_id=None, storage_dir=None)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {
        "prompts": [{"positive": "", "negative": ""}],
        "mode": mode,
        "form_data": dict(bound.values),
    })
    return next(pipe for pipe in pipes if pipe["name"] == pipe_name)["config"]


@pytest.mark.parametrize("mode", ["upscale", "video_upscale"])
def test_an_upscale_mode_sends_full_strength_and_no_short_side_by_default(template, mode):
    config = _pipe_config(template, mode, "upscaler/rtx_vsr", dict(SOURCES[mode]))

    assert float(config["target_short_side"]) == 0
    assert float(config["strength"]) == 1.0
    assert float(config["scale"]) == 2.0


@pytest.mark.parametrize("mode", ["upscale", "video_upscale"])
def test_an_upscale_mode_passes_the_short_side_and_the_strength_to_the_pipe(template, mode):
    config = _pipe_config(template, mode, "upscaler/rtx_vsr", {
        **SOURCES[mode], "intent": "upscale_highbitrate", "target_short_side": 1080, "strength": 0.5,
    })

    assert float(config["target_short_side"]) == 1080
    assert float(config["strength"]) == 0.5
    assert config["intent"] == "upscale_highbitrate"
