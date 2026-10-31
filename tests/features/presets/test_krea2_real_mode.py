from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.preset_render import build_fixture_form_data, build_generation_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

TRIGGER = "transform the image to realistic photograph"
LORA_SHA256 = "6455a87697f0202c5490480a89976562ae524fb551877752bc3f5fa54e1858c7"


@pytest.fixture(scope="module")
def krea2():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/Krea2")), None)
    if template is None:
        pytest.fail("marketplace/Krea2 preset not present")
    return template


def _pipes(template, form_over=None, positive=None):
    form_data = build_fixture_form_data(template, "real")
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    generation = build_generation_data("real", form_data, template.modes.get("real"))
    if positive is not None:
        generation["prompts"] = [{**pair, "positive": positive} for pair in generation["prompts"]]
    return processor.process(template, generation)


def _named(pipes, name):
    return next(p for p in pipes if p["name"] == name)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _fields(fields):
    for field in fields:
        yield field
        if isinstance(field.children, list):
            yield from _fields(field.children)


def test_real_is_a_core_mode(krea2):
    assert "real" in krea2.modes
    assert krea2.modes["real"].source_plugin is None


def test_the_generator_uses_the_ostris_reference_scheme(krea2):
    config = _named(_pipes(krea2), "generator/krea2_edit")["config"]
    assert config["ref_style"] == "ostris"
    assert config["guidance"] == 1.0
    assert config["ref_pixels"] == 1048576


def test_the_encoder_labels_pictures_and_caps_them_by_area(krea2):
    pipes = _pipes(krea2)
    encoder = _named(pipes, "prompt_encoder")
    assert ("image", "media_loader", "image") in _wired(encoder)
    assert encoder["config"]["picture_labels"] is True
    assert encoder["config"]["grounding_area"] == 384 * 384
    assert encoder["config"]["grounding_px"] == 0


def test_the_vision_tower_is_loaded(krea2):
    assert _named(_pipes(krea2), "model_loader/krea2")["config"]["vision"] is True


def test_the_trigger_leads_the_instruction_and_the_user_text_follows(krea2):
    encoder = _named(_pipes(krea2), "prompt_encoder")["config"]
    assert encoder["p_prompt"]["output"].startswith(TRIGGER + ". ")
    assert encoder["p_prompt"]["output"].endswith(encoder["p_prompt"]["input"])


def test_an_empty_prompt_leaves_just_the_trigger(krea2):
    pipes = _pipes(krea2, positive="")
    assert _named(pipes, "prompt_encoder")["config"]["p_prompt"]["output"] == TRIGGER


def test_the_trigger_phrase_can_be_replaced(krea2):
    encoder = _named(_pipes(krea2, {"photo_trigger": "make it a photo"}), "prompt_encoder")["config"]
    assert encoder["p_prompt"]["output"].startswith("make it a photo. ")


@pytest.mark.parametrize("size,pixels", [("1", 1048576), ("2", 2097152), ("4", 4194304)])
def test_output_size_maps_to_a_pixel_budget(krea2, size, pixels):
    assert _named(_pipes(krea2, {"output_size": size}), "generator/krea2_edit")["config"]["output_pixels"] == pixels


def test_the_real_lora_is_applied_ahead_of_extra_loras(krea2):
    extra = [{"model": "/m/style.safetensors", "strength": 0.4}]
    pipes = _pipes(krea2, {"real_lora": "/m/real.safetensors", "real_lora_strength": 0.8, "loras": extra})
    loras = _named(pipes, "model_loader/krea2")["config"]["loras"]
    assert [(entry["file_path"], entry["weight"]) for entry in loras] == [
        ("/m/real.safetensors", 0.8),
        ("/m/style.safetensors", 0.4),
    ]


def test_the_lora_recommendation_is_pinned_and_required(krea2):
    fields = {f.name: f for f in _fields(krea2.modes["real"].forms[0].fields) if f.name}
    field = fields["real_lora"]
    assert field.required is True
    assert [r["sha256"] for r in field.configuration["recommendations"]] == [LORA_SHA256]
    assert field.configuration["recommendations"][0]["provider"] == "civitai"


def test_no_negative_prompt_is_offered(krea2):
    assert krea2.modes["real"].forms[0].negative_prompt == {"applies_when": False}
