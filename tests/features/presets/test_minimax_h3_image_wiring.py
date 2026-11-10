from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET = "marketplace/MiniMax-H3-Image"
LOADER = "model_loader/minimax_h3_image"
GENERATOR = "generator/minimax_h3_image"


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if PRESET in Path(p.path).as_posix()), None)
    if found is None:
        pytest.fail(f"{PRESET} preset not present")
    return found


def _process(template, mode: str, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/minimax_h3_image_pruned_int8_convrot.safetensors",
        "text_encoder": "/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae": "/models/minimax_h3_image_vae_fp16.safetensors",
    }
    form_data.update(form_over or {})
    form_data = bound_form_data(template, mode, form_data)
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _edge(name, provider, output):
    return {"name": name, "provider": provider, "output_var": output, "enabled": True}


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def test_two_modes_on_the_minimax_h3_image_pipes(template):
    assert list(template.modes.keys()) == ["txt2img", "edit"]
    names = [p["name"] for p in _process(template, "txt2img")]
    assert LOADER in names and GENERATOR in names


def test_text_to_image_runs_six_steps_at_the_tuned_size_with_no_negative_prompt(template):
    pipes = _process(template, "txt2img")
    generator = _pipe(pipes, GENERATOR)["config"]
    assert generator["steps"] == 6
    assert generator["resolution"] == "2048x2048"
    assert generator["mode"] == "txt2img"
    encoder = _pipe(pipes, "prompt_encoder")["config"]
    assert float(encoder["guidance_scale"]) == 1.0
    assert "n_prompt" not in encoder


def test_the_three_picked_files_reach_the_loader(template):
    loader = _pipe(_process(template, "txt2img"), LOADER)["config"]
    assert loader["diffusion_model"]["file_path"].endswith("minimax_h3_image_pruned_int8_convrot.safetensors")
    assert loader["text_encoder"]["file_path"].endswith("qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors")
    assert loader["vae"]["file_path"].endswith("minimax_h3_image_vae_fp16.safetensors")


def test_edit_pictures_feed_both_the_text_encoder_and_the_generator(template):
    pipes = _process(template, "edit", {"references": ["/media/a.png", "/media/b.png"]})
    loader = _pipe(pipes, "references")
    assert loader["enabled"] is True
    assert [item["path"] for item in loader["config"]["media"]] == ["/media/a.png", "/media/b.png"]
    assert _edge("image", "references", "image") in _pipe(pipes, "prompt_encoder")["input"]
    generator = _pipe(pipes, GENERATOR)
    assert _edge("reference_images", "references", "image") in generator["input"]
    assert generator["config"]["mode"] == "edit"
    assert generator["config"]["resolution"] == "auto"


def test_edit_can_use_an_explicit_size_instead_of_picture_one(template):
    pipes = _process(template, "edit", {"references": ["/media/a.png"], "match_picture_1": False,
                                        "resolution": "2720x1536"})
    assert _pipe(pipes, GENERATOR)["config"]["resolution"] == "2720x1536"


def test_the_model_pickers_never_offer_the_unsupported_w4a8_file():
    text = Path("content/presets/marketplace/MiniMax-H3-Image/files/form/models.yml").read_text(encoding="utf-8")
    assert "w4a8" not in text
    assert "minimax_h3_image_pruned_int8_convrot.safetensors" in text
