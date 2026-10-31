from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

ESRGAN_X4_SHA256 = "aa00f09ad753d88576b21ed977e97d634976377031b178acc3b5b238df463400"


@pytest.fixture(scope="module")
def krea2():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/Krea2")), None)
    if template is None:
        pytest.fail("marketplace/Krea2 preset not present")
    return template


def _pipes(template, form_over=None):
    form_data = build_fixture_form_data(template, "upscale")
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": "upscale", "form_data": form_data})


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


def _refiner(pipes):
    return next(p for p in pipes if p["name"] == "tiled_refiner")


def _fields(fields):
    for field in fields:
        yield field
        if isinstance(field.children, list):
            yield from _fields(field.children)


def test_upscale_is_listed_after_enhance(krea2):
    assert list(krea2.modes)[:3] == ["txt2img", "enhance", "upscale"]


def test_upscale_refines_tile_by_tile_with_the_generation_model_and_prompt(krea2):
    pipes = _pipes(krea2)
    refiner = _refiner(pipes)
    assert _wired(refiner) == {
        ("image", "media_loader", "image"),
        ("model", "model_loader/krea2", "model"),
        ("conditioning", "prompt_encoder", "conditioning"),
        ("seed", "seed_generator", "seed"),
    }
    assert not any(p["name"].startswith("generator/") for p in pipes)
    gallery = next(p for p in pipes if p["name"] == "gallery")
    assert ("image", "tiled_refiner", "image") in _wired(gallery)


@pytest.mark.parametrize("factor,expected", [("2", 2.0), ("3", 3.0), ("4", 4.0)])
def test_the_upscale_factor_reaches_the_refiner_as_a_number(krea2, factor, expected):
    assert _refiner(_pipes(krea2, {"upscale_factor": factor}))["config"]["scale"] == expected


@pytest.mark.parametrize("amount,expected", [("subtle", 0.2), ("balanced", 0.3), ("strong", 0.45)])
def test_added_detail_maps_to_the_per_tile_denoise(krea2, amount, expected):
    assert _refiner(_pipes(krea2, {"refine_amount": amount}))["config"]["denoise"] == expected


def test_tile_geometry_and_calming_pass_through(krea2):
    config = _refiner(_pipes(krea2, {"tile_size": 768, "tile_overlap": 96, "content_aware": False}))["config"]
    assert (config["tile_size"], config["tile_overlap"], config["content_aware"]) == (768, 96, False)


def test_a_blank_upscaler_model_means_a_plain_resize(krea2):
    assert _refiner(_pipes(krea2, {"upscale_model": ""}))["config"]["upscale_model"] == ""


def test_turbo_defaults_run_eight_steps_without_guidance(krea2):
    config = _refiner(_pipes(krea2))["config"]
    assert (config["steps"], config["guidance"], config["sampler"]) == (8, 1.0, "euler")


def test_cfg_reaches_the_encoder_and_the_refiner(krea2):
    pipes = _pipes(krea2, {"cfg": 3.0})
    assert next(p for p in pipes if p["name"] == "prompt_encoder")["config"]["guidance_scale"] == 3.0
    assert _refiner(pipes)["config"]["guidance"] == 3.0


def test_the_upscaler_recommendation_is_pinned_and_optional(krea2):
    fields = {f.name: f for f in _fields(krea2.modes["upscale"].forms[0].fields) if f.name}
    field = fields["upscale_model"]
    assert not field.required
    assert field.configuration["model_type"] == "upscaler"
    assert [r["sha256"] for r in field.configuration["recommendations"]] == [ESRGAN_X4_SHA256]
