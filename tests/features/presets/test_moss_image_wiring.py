from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data


@pytest.fixture(scope="module")
def moss_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/MOSS-Image" in Path(p.path).as_posix()), None)
    if template is None:
        pytest.fail("marketplace/MOSS-Image preset not present")
    return template


def _process(template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/moss_image_sft_bf16.safetensors",
        "text_encoder": "/models/qwen3.5_9b_int8_convrot.safetensors",
        "vae": "/models/flux2-vae.safetensors",
        "resolution": "1024x1024",
    }
    if form_over:
        form_data.update(form_over)
    form_data = bound_form_data(template, "txt2img", form_data)
    generation_data = {"prompts": [], "mode": "txt2img", "form_data": form_data}
    return processor.process(template, generation_data)


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _names(pipes):
    return [p["name"] for p in pipes]


def test_modes_and_display_name(moss_template):
    assert list(moss_template.modes.keys()) == ["txt2img", "inpaint"]
    assert moss_template.name == "MOSS-Image"


def test_pipe_names_are_the_moss_image_family(moss_template):
    pipes = _process(moss_template)
    assert "model_loader/moss_image" in _names(pipes)
    assert "generator/moss_image" in _names(pipes)


def test_default_profile_is_base_with_concrete_steps_and_cfg(moss_template):
    cfg = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert int(cfg["steps"]) == 50
    assert float(cfg["guidance"]) == 3.0
    assert cfg["sampler"] == "euler"
    assert cfg["schedule"] == "moss_grid"


def test_default_profile_still_encodes_the_negative_prompt(moss_template):
    cfg = _pipe(_process(moss_template), "prompt_encoder")["config"]
    assert float(cfg["guidance_scale"]) == 3.0


def test_cfg_zero_star_is_off(moss_template):
    cfg = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert cfg["guidance_options"]["cfg_zero_star"] is False


def test_layer_skip_defaults_on_and_the_toggle_turns_it_off(moss_template):
    on = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert on["guidance_options"]["uncond_skip_layers"] == "22"
    off = _pipe(_process(moss_template, {"uncond_layer_skip": False}), "generator/moss_image")["config"]
    assert off["guidance_options"]["uncond_skip_layers"] == ""


def test_explicit_values_reach_the_generator(moss_template):
    cfg = _pipe(_process(moss_template, {"steps": 30, "cfg": 2.0}), "generator/moss_image")["config"]
    assert int(cfg["steps"]) == 30
    assert float(cfg["guidance"]) == 2.0


def test_blank_shift_is_omitted_and_override_reaches_the_generator(moss_template):
    blank = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert blank.get("shift") in (None, "")
    override = _pipe(_process(moss_template, {"shift": 4.0}), "generator/moss_image")["config"]
    assert float(override["shift"]) == 4.0


def test_resolution_reaches_the_generator(moss_template):
    cfg = _pipe(_process(moss_template, {"resolution": "1344x768"}), "generator/moss_image")["config"]
    assert cfg["resolution"] == "1344x768"


def test_generator_has_no_edit_wiring(moss_template):
    cfg = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert "mode" not in cfg


def test_step_cache_defaults_to_off(moss_template):
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    cfg = _pipe(_process(moss_template), "generator/moss_image")["config"]
    assert cfg["step_cache"] == {"rel_threshold": 0.0, "warmup_steps": 4, "max_consecutive_skips": 3}
    assert StepCacheSet(cfg["step_cache"]).enabled is False


def test_step_cache_form_values_reach_the_generator(moss_template):
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    form = {"step_cache_threshold": 0.12, "step_cache_warmup_steps": 6, "step_cache_max_skips": 2}
    cfg = _pipe(_process(moss_template, form), "generator/moss_image")["config"]
    assert cfg["step_cache"] == {"rel_threshold": 0.12, "warmup_steps": 6, "max_consecutive_skips": 2}
    assert isinstance(cfg["step_cache"]["rel_threshold"], float)
    assert isinstance(cfg["step_cache"]["warmup_steps"], int)
    assert StepCacheSet(cfg["step_cache"]).enabled is True


def test_loader_takes_the_three_models_and_the_lora_stack(moss_template):
    cfg = _pipe(_process(moss_template), "model_loader/moss_image")["config"]
    assert set(cfg) == {"diffusion_model", "text_encoder", "vae", "loras"}
    assert cfg["loras"] == []


def test_form_loras_reach_the_loader_and_the_run_record(moss_template):
    picked = [
        {"model": "/models/loras/style.safetensors", "strength": 0.7},
        {"model": "/models/loras/off.safetensors", "strength": 0},
    ]
    pipes = _process(moss_template, {"loras": picked})
    loras = _pipe(pipes, "model_loader/moss_image")["config"]["loras"]
    assert [(l["file_path"], float(l["weight"])) for l in loras] == [
        ("/models/loras/style.safetensors", 0.7),
        ("/models/loras/off.safetensors", 0.0),
    ]
    rows = _pipe(pipes, "param_emitter")["config"]["parameters"]
    flat = [pair for row in rows for pair in (row if isinstance(row[0], list) else [row])]
    recorded = [value for key, value in flat if key == "model"]
    assert "/models/loras/style.safetensors" in recorded
    assert "/models/loras/off.safetensors" not in recorded


def test_lora_tab_is_a_plain_picker_without_step_windows():
    tab = yaml.safe_load(
        Path("content/presets/marketplace/MOSS-Image/modes/txt2img/tabs/lora.yml").read_text(encoding="utf-8")
    )
    picker = tab["fields"][0]
    assert picker["type"] == "lora_picker"
    assert picker["configuration"]["filter_tags"] == "@config:lora_tags"
    assert "row_fields" not in picker["configuration"]
