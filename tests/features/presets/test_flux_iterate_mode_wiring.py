from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/Flux2")


@pytest.fixture(scope="module")
def flux_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/Flux2" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/Flux2 preset not present")
    return template


def _process(flux_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/flux_dit.safetensors",
        "text_encoder": "/models/qwen3_te.safetensors",
        "vae": "/models/flux_vae.safetensors",
        "resolution": "1024x1024",
    }
    if form_over:
        form_data.update(form_over)
    # bind_form fills every field's own `default:` server-side, typed --
    # exactly what a real generation request goes through before
    # PresetProcessor ever renders (docs/presets.md "Form binding and
    # validation").
    bound = bind_form(
        flux_template, "txt2img", form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {"prompts": [], "mode": "txt2img", "form_data": dict(bound.values)}
    return processor.process(flux_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


# -- rendering: the values land with the declared types ---------------------

def test_iterate_mode_defaults_to_off(flux_template):
    cfg = _pipe(_process(flux_template), "generator/flux")["config"]
    assert cfg["iterate_mode"] is False


def test_iterate_mode_on_reaches_the_generator(flux_template):
    cfg = _pipe(_process(flux_template, {"iterate_mode": True}), "generator/flux")["config"]
    assert cfg["iterate_mode"] is True


# -- form definition ---------------------------------------------------------

def _field_by_name(fields, name):
    for f in fields:
        if f.get("name") == name:
            return f
        if "children" in f:
            found = _field_by_name(f["children"], name)
            if found is not None:
                return found
    return None


@pytest.fixture(scope="module")
def advanced_tab():
    return yaml.safe_load((PRESET_DIR / "modes/txt2img/tabs/advanced.yml").read_text())


def test_iterate_mode_checkbox_is_present(advanced_tab):
    field = _field_by_name(advanced_tab["fields"], "iterate_mode")
    assert field is not None
    assert field["type"] == "checkbox"
    assert field["default"] is False
