"""Tests for the LTX-2 two-stage refine's stage-2-only LoRA picker:
Enhance tab's `enhancement_loras` field flattens into
`generator_stage2`'s `stage2_loras` config the same way the main LoRA tab
flattens into `model_loader/ltx`'s `loras` config.
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor
from src.features.video_director.normalize import derive_ltx_media_fields

PRESET_DIR = Path("content/presets/marketplace/LTX-2")

_DOC_T2V = {
    "settings": {"seed": 123, "duration": 5, "fps": 25},
    "segments": [{"prompt": "a cat", "negative_prompt": "ugly"}],
    "media": [], "audio": [], "ic_lora": [],
}


@pytest.fixture(scope="module")
def ltx_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if str(p.path).endswith("native/LTX-2")), None)
    if template is None:
        pytest.skip("native/LTX-2 preset not present")
    return template


def _pipe(pipes, pid):
    return next(p for p in pipes if p.get("id") == pid or p["name"] == pid)


def _process(ltx_template, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    doc = copy.deepcopy(_DOC_T2V)
    doc.update(derive_ltx_media_fields(doc["media"], doc["ic_lora"], doc["settings"].get("fps")))
    form_data = {
        "model": "/models/ltx.safetensors",
        "text_encoder": "/models/gemma3.safetensors",
        "resolution": "768x512",
        "video_director": doc,
        "upscale_refine_sigmas": "",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video", "form_data": form_data}
    return processor.process(ltx_template, generation_data)


# -- rendering: generator_stage2's stage2_loras -----------------------------

def test_upscale_off_stage2_disabled_config_untouched_byte_identical(ltx_template):
    # Default fixture (Upscale off): generator_stage2 stays disabled and its
    # config is never rendered at all -- adding stage2_loras must not
    # perturb this in any way (the golden preset-render fixture pins it).
    pipes = _process(ltx_template)
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["enabled"] is False
    assert stage2["config"] == {}


def test_upscale_on_empty_picker_renders_empty_stage2_loras_list(ltx_template):
    pipes = _process(ltx_template, {"upscale": "1.5x", "upscale_model": "/models/upscaler.safetensors"})
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["enabled"] is True
    assert stage2["config"]["stage2_loras"] == []


def test_upscale_on_picker_flattens_to_file_path_and_weight(ltx_template):
    pipes = _process(ltx_template, {
        "upscale": "1.5x", "upscale_model": "/models/upscaler.safetensors",
        "enhancement_loras": [
            {"model": "/models/loras/distilled.safetensors", "strength": 1.0},
            {"model": "/models/loras/detail.safetensors", "strength": 0.6},
        ],
    })
    stage2 = _pipe(pipes, "generator_stage2")
    assert stage2["config"]["stage2_loras"] == [
        {"file_path": "/models/loras/distilled.safetensors", "weight": 1.0},
        {"file_path": "/models/loras/detail.safetensors", "weight": 0.6},
    ]


def test_stage1_config_never_carries_stage2_loras(ltx_template):
    # stage2_loras is a stage-2-only knob -- generator_stage1's own config
    # (and model_loader/ltx's `loras`) must be unaffected by the picker.
    pipes = _process(ltx_template, {
        "upscale": "1.5x", "upscale_model": "/models/upscaler.safetensors",
        "enhancement_loras": [{"model": "/models/loras/distilled.safetensors", "strength": 1.0}],
    })
    stage1 = _pipe(pipes, "generator_stage1")
    loader = _pipe(pipes, "model_loader/ltx")
    assert "stage2_loras" not in stage1["config"]
    assert loader["config"]["loras"] == []


# -- form-definition assertions ---------------------------------------------

def _field_by_name(fields, name):
    for f in fields:
        if f.get("name") == name:
            return f
        if "children" in f:
            found = _field_by_name(f["children"], name)
            if found is not None:
                return found
    return None


def test_enhancement_loras_field_is_lora_picker_hidden_when_upscale_off():
    tab_path = PRESET_DIR / "modes/video/tabs/enhance.yml"
    data = yaml.safe_load(tab_path.read_text())
    field = _field_by_name(data["fields"], "enhancement_loras")
    assert field is not None
    assert field["type"] == "lora_picker"
    assert field["default"] == []
    reactions = field.get("reactions", [])
    off_reaction = next(r for r in reactions if r["when"] == {"field": "upscale", "equals": "off"})
    assert off_reaction["then"] == {"set_visibility": False}
    on_reaction = next(r for r in reactions if r["when"] == {"field": "upscale", "not_equals": "off"})
    assert on_reaction["then"] == {"set_visibility": True}


def test_enhancement_loras_shares_lora_tags_filter_with_main_lora_field():
    lora_tab = yaml.safe_load((PRESET_DIR / "modes/video/tabs/lora.yml").read_text())
    enhance_tab = yaml.safe_load((PRESET_DIR / "modes/video/tabs/enhance.yml").read_text())
    main_field = _field_by_name(lora_tab["fields"], "loras")
    stage2_field = _field_by_name(enhance_tab["fields"], "enhancement_loras")
    assert main_field["configuration"]["filter_tags"] == stage2_field["configuration"]["filter_tags"]
