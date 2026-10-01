from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def sdxl_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/SDXL" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/SDXL preset not present")
    return template


def _render(template, form_over):
    form_data = {"model": "/models/sdxl.safetensors", "source_image": "uploads/room.png", **form_over}
    bound = bind_form(template, "inpaint", form_name=None, raw_form_data=form_data, user_id=None, storage_dir=None)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    pipes = processor.process(template, {"prompts": [], "mode": "inpaint", "form_data": dict(bound.values)})
    return bound, {p.get("id") or p["name"]: p for p in pipes}


def test_a_painted_mask_reaches_the_inpaint_pipeline(sdxl_template):
    bound, pipes = _render(sdxl_template, {"source_image_inpaint_mask": "uploads/room_mask.png"})
    assert bound.values["source_image_inpaint_mask"] == "uploads/room_mask.png"
    assert pipes["mask_loader"]["enabled"] is True
    assert pipes["mask_loader"]["config"]["media"][0]["path"] == "uploads/room_mask.png"
    for name in ("mask_preprocessor", "inpaint_region_crop", "inpaint_region_restore"):
        assert pipes[name]["enabled"] is True
    assert pipes["generator"]["config"]["inpaint_mode"] is True


def test_without_a_mask_the_inpaint_pipeline_stays_off(sdxl_template):
    _bound, pipes = _render(sdxl_template, {})
    assert pipes["mask_loader"]["enabled"] is False
    assert pipes["generator"]["config"]["inpaint_mode"] is False
