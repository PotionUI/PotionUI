from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET_DIR = Path("content/presets/marketplace/QwenImage")
LORA = "/models/anything2real_2601.safetensors"
LORA_SHA256 = "6ffd4a1e15e882782b8ac83ed8efabe52113ce7d2796ad7b1913f80941668c0f"
BASE = "change the picture 1 to realistic photograph"


@pytest.fixture(scope="module")
def qwen_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/QwenImage")), None)
    if template is None:
        pytest.fail("marketplace/QwenImage preset not present")
    return template


def _process(template, prompts=None, **form_over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "diffusion_model": "/models/qwen_edit_2511.safetensors",
        "text_encoder": "/models/qwen_te.safetensors",
        "vae": "/models/qwen_vae.safetensors",
        "real_lora": LORA,
        "source_image": "/media/art.png",
    }
    form_data.update(form_over)
    form_data = bound_form_data(template, "real", form_data)
    pairs = prompts or [{"positive": "", "negative": ""}]
    pipes = processor.process(template, {"prompts": pairs, "mode": "real", "form_data": form_data})
    return {p.get("id") or p["name"]: p for p in pipes}


def _final_prompts(pipes, count):
    config = pipes["prompt_encoder"]["config"]
    pipe = PromptEncoderPipe({**PromptEncoderPipe.get_default_config(), **config})
    return [
        pipe._final_prompt_pair(
            config["p_prompt"]["output"], config["p_prompt"]["input"],
            config["n_prompt"]["output"], config["n_prompt"]["input"], config["pairs"], index,
        )[0]
        for index in range(count)
    ]


def test_an_empty_prompt_runs_the_bare_conversion(qwen_template):
    pipes = _process(qwen_template)
    assert _final_prompts(pipes, 1) == [BASE]


def test_the_description_follows_the_conversion_phrase(qwen_template):
    prompts = [{"positive": " a woman in a red coat ", "negative": ""}, {"positive": "a cat", "negative": ""}]
    pipes = _process(qwen_template, prompts=prompts, quantity=2)
    assert _final_prompts(pipes, 2) == [f"{BASE}, a woman in a red coat", f"{BASE}, a cat"]


def test_the_lora_loads_first_at_full_strength_then_the_users(qwen_template):
    extra = [{"model": "/models/extra.safetensors", "strength": 0.4}]
    loras = _process(qwen_template, loras=extra)["model_loader/qwen"]["config"]["loras"]
    assert loras == [{"file_path": LORA, "weight": 1.0}, {"file_path": "/models/extra.safetensors", "weight": 0.4}]
    loras = _process(qwen_template, real_strength=0.8)["model_loader/qwen"]["config"]["loras"]
    assert loras[0]["weight"] == 0.8


def test_the_run_is_an_edit_of_the_source_image(qwen_template):
    pipes = _process(qwen_template)
    assert pipes["generator/qwen"]["config"]["mode"] == "edit"
    assert pipes["media_loader"]["config"]["media"] == [{"type": "image", "path": "/media/art.png"}]


def test_the_prompt_is_optional_and_the_mode_registered(qwen_template):
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert "real" in preset["modes"]
    assert "real" in preset["vars"]["optional_prompt_modes"]
    assert "real" not in preset["vars"]["promptless_modes"]


def test_the_lora_recommendation_is_pinned_to_the_published_file():
    form = yaml.safe_load((PRESET_DIR / "modes/real/form.yml").read_text(encoding="utf-8"))
    assert form["requirements"][0]["filename"] == "anything2real_2601.safetensors"
    tab = yaml.safe_load((PRESET_DIR / "modes/real/tabs/generation.yml").read_text(encoding="utf-8"))
    field = next(
        child for section in tab["fields"] for child in section.get("children", []) if child.get("name") == "real_lora"
    )
    rec = field["configuration"]["recommendations"][0]
    assert rec["sha256"] == LORA_SHA256
    assert "lrzjason/Anything2Real" in rec["link"]
    assert field["required"] is True
