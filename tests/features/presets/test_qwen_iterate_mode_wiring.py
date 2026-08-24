"""Tests that the QwenImage preset's `txt2img` mode wires `iterate_mode`
(Advanced tab) into `generator/qwen`'s config -- the field is declared on
`GeneratorQwenPipe.configuration()` via the shared
`iterate_mode_config_specs()` splice (see
tests/pipelines/pipes/generator/qwen/test_qwen_generator.py for the
declaration-level test).

Mirrors tests/features/presets/test_flux_iterate_spectral_wiring.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/QwenImage")


@pytest.fixture(scope="module")
def qwen_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/QwenImage" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/QwenImage preset not present")
    return template


def _process(qwen_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen_dit.safetensors",
        "text_encoder": "/models/qwen_te.safetensors",
        "vae": "/models/qwen_vae.safetensors",
        "resolution": "1024x1024",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "txt2img", "form_data": form_data}
    return processor.process(qwen_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def test_iterate_mode_defaults_to_off(qwen_template):
    cfg = _pipe(_process(qwen_template), "generator/qwen")["config"]
    assert cfg["iterate_mode"] is False


def test_iterate_mode_on_reaches_the_generator(qwen_template):
    cfg = _pipe(_process(qwen_template, {"iterate_mode": True}), "generator/qwen")["config"]
    assert cfg["iterate_mode"] is True


def _field_by_name(fields, name):
    for f in fields:
        if f.get("name") == name:
            return f
        if "children" in f:
            found = _field_by_name(f["children"], name)
            if found is not None:
                return found
    return None


def test_iterate_mode_checkbox_is_present():
    tab = yaml.safe_load((PRESET_DIR / "modes/txt2img/tabs/advanced.yml").read_text())
    field = _field_by_name(tab["fields"], "iterate_mode")
    assert field is not None
    assert field["type"] == "checkbox"
    assert field["default"] is False


def test_iterate_mode_declared_on_the_generator_spec():
    # Discoverability: the knob is typed on generator/qwen itself (spliced
    # from the shared iterate_mode_config_specs()), not just an undeclared
    # passthrough the pipe happens to read.
    from src.pipelines.pipes.generator.qwen.main import GeneratorQwenPipe

    specs = {s.name: s for s in GeneratorQwenPipe.configuration()}
    assert "iterate_mode" in specs
    assert specs["iterate_mode"].param_type is bool
    assert specs["iterate_mode"].default is False
