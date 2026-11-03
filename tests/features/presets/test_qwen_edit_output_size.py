from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

CASES = {
    "qwen21_combine": ("marketplace/QwenImage-2.1", "combine", "images", "generator/qwen_image21"),
    "qwen10_edit": ("marketplace/QwenImage", "edit", "source_image", "generator/qwen"),
}


@pytest.fixture(scope="module")
def presets():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {Path(p.path).as_posix().split("content/presets/")[-1]: p for p in loader.presets}


def _generator_config(presets, case, **over):
    preset_key, mode, image_field, generator = CASES[case]
    template = presets[preset_key]
    form = {
        "diffusion_model": "/models/dit.safetensors",
        "text_encoder": "/models/te.safetensors",
        "vae": "/models/vae.safetensors",
        image_field: ["/uploads/base.png", "/uploads/ref.png"],
        **over,
    }
    form = bound_form_data(template, mode, form)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {"prompts": [], "mode": mode, "form_data": form})
    return next(p for p in pipes if (p.get("id") or p["name"]) == generator)["config"]


@pytest.mark.parametrize("case", list(CASES))
def test_the_default_matches_the_first_image(presets, case):
    cfg = _generator_config(presets, case)
    assert cfg["fit"] == "match"
    assert cfg["resolution"] == "1024x1024"


@pytest.mark.parametrize("case", list(CASES))
def test_a_resolution_alone_keeps_matching_the_first_image(presets, case):
    assert _generator_config(presets, case, resolution="1664x928")["fit"] == "match"


@pytest.mark.parametrize("case", list(CASES))
def test_custom_sends_the_resolution_and_crops_by_default(presets, case):
    cfg = _generator_config(presets, case, output_size="custom", resolution="1664x928")
    assert (cfg["resolution"], cfg["fit"]) == ("1664x928", "crop")


@pytest.mark.parametrize("case", list(CASES))
def test_custom_with_pad_sends_pad(presets, case):
    cfg = _generator_config(presets, case, output_size="custom", resolution="928x1664", output_fit="pad")
    assert (cfg["resolution"], cfg["fit"]) == ("928x1664", "pad")


@pytest.mark.parametrize("case", list(CASES))
def test_an_unknown_output_size_is_rejected(presets, case):
    with pytest.raises(FormBindingError):
        _generator_config(presets, case, output_size="huge")

