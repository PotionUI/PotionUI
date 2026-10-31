from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

LORA = "ltx2.3-transition.safetensors"
LORA_SHA256 = "ba420d6fefafced8e317e2d6ff951b312b52f534377d016b491877a00b830d33"
MODE_ROOT = Path("content/presets/marketplace/LTX-2.5/modes/transition")


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5")), None)
    if found is None:
        pytest.fail("marketplace/LTX-2.5 preset not present")
    return found


def transition(template, prompt="", **over):
    form_data = {
        "first_image": "/media/first.png",
        "last_image": "/media/last.png",
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "manual_sigmas": "",
        **over,
    }
    form_data = bound_form_data(template, "transition", form_data)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    generation = {"prompts": [{"positive": prompt, "negative": ""}], "mode": "transition", "form_data": form_data}
    return {pipe.get("id") or pipe["name"]: pipe for pipe in processor.process(template, generation)}


def test_the_first_picture_opens_the_video_and_the_last_picture_closes_it(template):
    pipes = transition(template)
    assert pipes["media_loader"]["config"]["media"] == [
        {"type": "image", "path": "/media/first.png"},
        {"type": "image", "path": "/media/last.png"},
    ]
    generator = pipes["generator"]
    assert ("image", "media_loader", "image") in {
        (edge["name"], edge["provider"], edge["output_var"]) for edge in generator["input"]
    }
    assert generator["config"]["media_placements"] == [
        {"source": "image", "index": 0, "frame": "first", "strength": 1.0, "role": "keyframe"},
        {"source": "image", "index": 1, "frame": "last", "strength": 1.0, "role": "keyframe"},
    ]


@pytest.mark.parametrize("seconds,frames", [(1, 24), (5, 120), (10, 240)])
def test_the_length_is_chosen_in_seconds(template, seconds, frames):
    assert transition(template, duration=seconds)["generator"]["config"]["frames"] == frames


def test_without_the_transition_lora_nothing_is_scoped_and_no_trigger_is_added(template):
    pipes = transition(template, prompt="the room fades to night", effect_lora="")
    assert pipes["generator"]["config"]["scoped_loras"][0]["file_path"] == ""
    caption = pipes["prompt_encoder"]["config"]["p_prompt"]["output"]
    assert caption == "the room fades to night"
    assert "zhuanchang" not in caption


def test_with_the_transition_lora_the_trigger_word_ends_the_caption_and_the_strength_applies(template):
    pipes = transition(template, prompt="the room fades to night", effect_lora="/models/" + LORA, effect_strength=0.8)
    assert pipes["prompt_encoder"]["config"]["p_prompt"]["output"] == "the room fades to night, zhuanchang"
    assert pipes["generator"]["config"]["scoped_loras"] == [{"file_path": "/models/" + LORA, "weight": 0.8}]


def test_an_empty_prompt_still_describes_a_transition(template):
    caption = transition(template, prompt="")["prompt_encoder"]["config"]["p_prompt"]["output"]
    assert "transition" in caption


def test_the_lora_is_optional_and_recommended_by_hash():
    generation = yaml.safe_load((MODE_ROOT / "tabs" / "generation.yml").read_text(encoding="utf-8"))
    section = next(f for f in generation["fields"] if f.get("label") == "Transition LoRA")
    (lora,) = section["children"]
    assert lora["name"] == "effect_lora" and lora["required"] is False
    (recommendation,) = lora["configuration"]["recommendations"]
    assert recommendation["name"] == LORA
    assert recommendation["sha256"] == LORA_SHA256
    assert re.fullmatch(r"\d+(\.\d+)? (MB|GB)", recommendation["size"])
    assert json.loads(recommendation["ref"])["file"] == LORA
    form = yaml.safe_load((MODE_ROOT / "form.yml").read_text(encoding="utf-8"))
    assert "requirements" not in form


def test_the_pictures_are_required_and_described_briefly():
    references = yaml.safe_load((MODE_ROOT / "tabs" / "references.yml").read_text(encoding="utf-8"))
    pictures = [f for f in references["fields"] if f["name"] in ("first_image", "last_image")]
    assert [f["name"] for f in pictures] == ["first_image", "last_image"]
    assert all(f["required"] and len(f["description"].split()) <= 6 for f in pictures)
