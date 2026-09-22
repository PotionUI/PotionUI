from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/QwenImage-2.1")


@pytest.fixture(scope="module")
def qwen_image21_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/QwenImage-2.1" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/QwenImage-2.1 preset not present")
    return template


def _process(qwen_image21_template, form_over: dict | None = None, mode: str = "txt2img"):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen21_dit.safetensors",
        "text_encoder": "/models/qwen21_te.safetensors",
        "vae": "/models/qwen21_vae.safetensors",
        "resolution": "1024x1024",
    }
    if mode == "edit":
        form_data["source_image"] = ["/uploads/source.png"]
    if form_over:
        form_data.update(form_over)
    bound = bind_form(
        qwen_image21_template, mode, form_name=None, raw_form_data=form_data,
        user_id=None, storage_dir=None,
    )
    generation_data = {"prompts": [], "mode": mode, "form_data": dict(bound.values)}
    return processor.process(qwen_image21_template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def test_model_loader_and_generator_pipe_names_are_the_2_1_family(qwen_image21_template):
    pipes = _process(qwen_image21_template)
    assert _pipe(pipes, "model_loader/qwen_image21") is not None
    assert _pipe(pipes, "generator/qwen_image21") is not None


def test_blank_shift_form_field_is_omitted_from_generator_config(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    assert cfg.get("shift") in (None, "")


def test_shift_override_reaches_the_generator(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {"shift": 1.5}), "generator/qwen_image21")["config"]
    assert float(cfg["shift"]) == 1.5


def test_default_speed_profile_reaches_steps_and_guidance(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    assert int(cfg["steps"]) == 40
    assert float(cfg["guidance"]) == 4.0


def test_txt2img_and_edit_modes_are_declared(qwen_image21_template):
    assert list(qwen_image21_template.modes.keys()) == ["txt2img", "edit"]


def test_edit_mode_loader_wires_vision_true(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    cfg = _pipe(pipes, "model_loader/qwen_image21")["config"]
    assert cfg["vision"] is True


def test_edit_mode_generator_config(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    cfg = _pipe(pipes, "generator/qwen_image21")["config"]
    assert cfg["mode"] == "edit"


def test_edit_mode_media_loader_and_prompt_encoder_share_the_source_image(qwen_image21_template):
    pipes = _process(qwen_image21_template, mode="edit")
    media = _pipe(pipes, "media_loader")["config"]["media"]
    assert len(media) == 1
    assert media[0]["path"] == "/uploads/source.png"


def test_step_cache_defaults_off_in_txt2img(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.0
    assert int(step_cache["warmup_steps"]) == 4
    assert int(step_cache["max_consecutive_skips"]) == 3


def test_step_cache_form_values_reach_the_generator_in_txt2img(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {
        "step_cache_threshold": 0.1,
        "step_cache_warmup_steps": 6,
        "step_cache_max_skips": 2,
    }), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.1
    assert int(step_cache["warmup_steps"]) == 6
    assert int(step_cache["max_consecutive_skips"]) == 2


def test_step_cache_form_values_reach_the_generator_in_edit(qwen_image21_template):
    cfg = _pipe(_process(qwen_image21_template, {
        "step_cache_threshold": 0.12,
        "step_cache_warmup_steps": 5,
        "step_cache_max_skips": 1,
    }, mode="edit"), "generator/qwen_image21")["config"]
    step_cache = cfg["step_cache"]
    assert float(step_cache["rel_threshold"]) == 0.12
    assert int(step_cache["warmup_steps"]) == 5
    assert int(step_cache["max_consecutive_skips"]) == 1
