from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET_DIR = Path("content/presets/marketplace/QwenImage")
LORA = "/models/qwen-image-edit-2511-multiple-angles-lora.safetensors"
LORA_SHA256 = "42426ded4e25fd22879d9e198b857556445ef4ca56e8da3246d0345155bb6765"

AZIMUTHS = [
    (0, "front view"),
    (45, "front-right quarter view"),
    (90, "right side view"),
    (135, "back-right quarter view"),
    (180, "back view"),
    (-135, "back-left quarter view"),
    (-90, "left side view"),
    (-45, "front-left quarter view"),
]
ELEVATIONS = [(-30, "low-angle shot"), (0, "eye-level shot"), (30, "elevated shot"), (60, "high-angle shot")]
DISTANCES = ["close-up", "medium shot", "wide shot"]


@pytest.fixture(scope="module")
def qwen_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/QwenImage")), None)
    if template is None:
        pytest.fail("marketplace/QwenImage preset not present")
    return template


def _process(template, **form_over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen_edit_2511.safetensors",
        "text_encoder": "/models/qwen_te.safetensors",
        "vae": "/models/qwen_vae.safetensors",
        "camera_lora": LORA,
        "source_image": "/media/source.png",
    }
    form_data.update(form_over)
    form_data = bound_form_data(template, "camera", form_data)
    return processor.process(template, {"prompts": [], "mode": "camera", "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _prompt(template, **form_over):
    return _pipe(_process(template, **form_over), "prompt_encoder")["config"]["p_prompt"]["input"]


@pytest.mark.parametrize("zoom", DISTANCES)
@pytest.mark.parametrize("tilt,shot", ELEVATIONS)
@pytest.mark.parametrize("turn,view", AZIMUTHS)
def test_every_dial_position_composes_the_lora_trigger_prompt(qwen_template, turn, view, tilt, shot, zoom):
    prompt = _prompt(qwen_template, camera_turn=turn, camera_tilt=tilt, camera_zoom=zoom)
    assert prompt == f"<sks> {view} {shot} {zoom}"


def test_the_default_dials_move_the_camera(qwen_template):
    assert _prompt(qwen_template) == "<sks> front-right quarter view eye-level shot medium shot"


def test_the_camera_lora_loads_first_at_the_advanced_strength(qwen_template):
    loras = _pipe(_process(qwen_template), "model_loader/qwen")["config"]["loras"]
    assert loras == [{"file_path": LORA, "weight": 0.9}]
    loras = _pipe(_process(qwen_template, camera_strength=1.0), "model_loader/qwen")["config"]["loras"]
    assert loras[0]["weight"] == 1.0


def test_user_loras_follow_the_camera_lora(qwen_template):
    extra = [{"model": "/models/extra.safetensors", "strength": 0.5}]
    loras = _pipe(_process(qwen_template, loras=extra), "model_loader/qwen")["config"]["loras"]
    assert [entry["file_path"] for entry in loras] == [LORA, "/models/extra.safetensors"]
    assert loras[1]["weight"] == 0.5


def test_the_run_is_an_edit_of_the_single_source_image(qwen_template):
    pipes = _process(qwen_template)
    assert _pipe(pipes, "generator/qwen")["config"]["mode"] == "edit"
    assert _pipe(pipes, "media_loader")["config"]["media"] == [{"type": "image", "path": "/media/source.png"}]
    assert _pipe(pipes, "model_loader/qwen")["config"]["vision"] is True


def test_the_mode_never_reads_the_written_prompt(qwen_template):
    pipes = _process(qwen_template)
    assert all(p["name"] != "dynamic_prompts_renderer" for p in pipes)
    assert _pipe(pipes, "prompt_encoder")["config"]["n_prompt"]["input"] == ""
    emitted = [entry for entry in _pipe(pipes, "param_emitter")["config"]["parameters"] if entry]
    assert ["positive_prompt", "<sks> front-right quarter view eye-level shot medium shot"] in emitted


def test_the_mode_is_promptless_and_requires_the_lora():
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert "camera" in preset["modes"]
    assert "camera" in preset["vars"]["promptless_modes"]
    form = yaml.safe_load((PRESET_DIR / "modes/camera/form.yml").read_text(encoding="utf-8"))
    assert {"type": "model", "filename": "qwen-image-edit-2511-multiple-angles-lora.safetensors",
            "hint": form["requirements"][0]["hint"]} == form["requirements"][0]


def test_the_lora_recommendation_is_pinned_to_the_published_file():
    tab = yaml.safe_load((PRESET_DIR / "modes/camera/tabs/generation.yml").read_text(encoding="utf-8"))
    field = next(
        child for section in tab["fields"] for child in section.get("children", []) if child.get("name") == "camera_lora"
    )
    rec = field["configuration"]["recommendations"][0]
    assert rec["sha256"] == LORA_SHA256
    assert "fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA" in rec["link"]
    assert field["required"] is True
