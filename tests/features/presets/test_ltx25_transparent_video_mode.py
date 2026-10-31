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

LORA = "ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors"
LORA_SHA256 = "d9e143f979e0756f0c83d772e6a8890f4c6b933db1f7d93fc06e25c179f3fd36"
MODE_ROOT = Path("content/presets/marketplace/LTX-2.5/modes/transparent_video")


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/LTX-2.5")), None)
    if found is None:
        pytest.fail("marketplace/LTX-2.5 preset not present")
    return found


def transparent(template, **over):
    form_data = {
        "input_video": "/media/clip.mp4",
        "model": "/models/ltx25_dit.safetensors",
        "text_encoder": "/models/gemma4.safetensors",
        "video_vae": "/models/ltx25_video_vae.safetensors",
        "effect_lora": "/models/" + LORA,
        "manual_sigmas": "",
        **over,
    }
    form_data = bound_form_data(template, "transparent_video", form_data)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    generation = {"prompts": [{"positive": "", "negative": ""}], "mode": "transparent_video", "form_data": form_data}
    return {pipe.get("id") or pipe["name"]: pipe for pipe in processor.process(template, generation)}


def wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def test_the_clip_is_the_reference_and_the_lora_is_scoped_to_the_generator(template):
    pipes = transparent(template)
    generator = pipes["generator"]
    assert ("video", "media_loader", "video") in wired(generator)
    assert generator["config"]["media_placements"] == [
        {"source": "video", "index": 0, "frame": "first", "strength": 1.0, "role": "reference"},
    ]
    assert generator["config"]["scoped_loras"] == [{"file_path": "/models/" + LORA, "weight": 1.0}]
    assert generator["config"]["audio"] is False
    assert generator["config"]["match_video"] is True


def test_the_prompt_is_always_empty(template):
    config = transparent(template)["prompt_encoder"]["config"]
    assert config["p_prompt"]["output"] == "" and config["n_prompt"]["output"] == ""
    assert config["pairs"] == [{"positive": "", "negative": ""}]


def test_the_matte_and_the_original_clip_feed_the_alpha_export(template):
    pipes = transparent(template)
    assert wired(pipes["alpha_video"]) == {
        ("video", "media_loader", "video"),
        ("matte", "generator", "video"),
    }
    assert ("video", "alpha_video", "video") in wired(pipes["gallery"])


def test_the_export_options_reach_the_pipe(template):
    default = transparent(template)["alpha_video"]["config"]
    assert default == {
        "black_point": 0.0, "white_point": 1.0, "crf": 24, "preview": True, "save_matte": False,
    }
    tuned = transparent(
        template,
        matte_black_point=0.05, matte_white_point=0.95, matte_quality=30,
        preview_over_checkerboard=False, save_matte=True,
    )["alpha_video"]["config"]
    assert tuned == {"black_point": 0.05, "white_point": 0.95, "crf": 30, "preview": False, "save_matte": True}


def test_the_mode_takes_no_prompt():
    form = yaml.safe_load((MODE_ROOT / "form.yml").read_text(encoding="utf-8"))
    assert form["prompt"] == "none"
    assert "prompt_label" not in form and "negative_prompt" not in form


def test_the_alpha_gen_lora_is_required_and_recommended_by_hash():
    form = yaml.safe_load((MODE_ROOT / "form.yml").read_text(encoding="utf-8"))
    assert form["requirements"][0]["filename"] == LORA
    generation = yaml.safe_load((MODE_ROOT / "tabs" / "generation.yml").read_text(encoding="utf-8"))
    section = next(f for f in generation["fields"] if f.get("label") == "Alpha-Gen LoRA")
    (lora,) = section["children"]
    assert lora["name"] == "effect_lora" and lora["required"] is True
    (recommendation,) = lora["configuration"]["recommendations"]
    assert recommendation["name"] == LORA
    assert recommendation["sha256"] == LORA_SHA256
    assert re.fullmatch(r"\d+(\.\d+)? (MB|GB)", recommendation["size"])
    assert json.loads(recommendation["ref"]) == {
        "repo": "Lightricks/LTX-2.5-22b-IC-LoRA-Alpha-Gen", "file": LORA, "revision": "main",
    }


def test_only_the_distilled_and_custom_speed_profiles_are_offered():
    generation = yaml.safe_load((MODE_ROOT / "tabs" / "generation.yml").read_text(encoding="utf-8"))
    speed = next(f for f in generation["fields"] if f.get("name") == "speed_profile")
    assert [o["value"] for o in speed["configuration"]["options"]] == ["distilled", "custom"]
