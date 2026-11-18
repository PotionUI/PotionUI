from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET_DIR = Path("content/presets/marketplace/QwenImage-2.1")
STALE_TURBO_LORA = "/models/turbo8_lora_step2500.safetensors"
TURBO_SHA256 = "8b74237a7f505773e2c66726fa696444b4c704585c93a347cff9044937f7914a"
MODES = ["txt2img", "edit", "combine", "text", "restyle"]
BASE = {
    "diffusion_model": "/models/dit.safetensors",
    "text_encoder": "/models/te.safetensors",
    "vae": "/models/vae.safetensors",
    "source_image": "/media/photo.png",
    "images": ["/media/photo.png"],
    "current_text": "old",
    "new_text": "new",
}


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/QwenImage-2.1")), None)
    if found is None:
        pytest.fail("marketplace/QwenImage-2.1 preset not present")
    return found


def _render(template, mode, **over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    form = bound_form_data(template, mode, {**BASE, **over})
    pipes = processor.process(template, {"prompts": [{"positive": "a cat", "negative": ""}], "mode": mode, "form_data": form})
    return {p.get("id") or p["name"]: p for p in pipes}


def _loras(pipes):
    return pipes["model_loader/qwen_image21"]["config"]["loras"]


def _emitted_models(pipes):
    flat = [e for item in pipes["param_emitter"]["config"]["parameters"] for e in (item if item and isinstance(item[0], list) else [item])]
    return [e[1] for e in flat if e and e[0] == "model"]


def test_the_turbo_profile_is_eight_steps_without_cfg():
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert preset["speed_profiles"]["turbo"] == {"steps": 8, "guidance": 1.0, "sampler": "euler"}
    assert preset["speed_profiles"]["balanced"]["steps"] == 40


@pytest.mark.parametrize("mode", MODES)
def test_turbo_runs_eight_steps_at_cfg_one_with_only_the_lora_tab_loras(template, mode):
    extra = [{"model": "/models/extra.safetensors", "strength": 0.6}]
    pipes = _render(template, mode, speed_profile="turbo", loras=extra)
    generator = pipes["generator/qwen_image21"]["config"]
    assert int(generator["steps"]) == 8
    assert float(generator["guidance"]) == 1.0
    assert _loras(pipes) == [{"file_path": "/models/extra.safetensors", "weight": 0.6}]


@pytest.mark.parametrize("mode", MODES)
def test_turbo_without_loras_loads_nothing_extra(template, mode):
    pipes = _render(template, mode, speed_profile="turbo")
    assert _loras(pipes) == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("profile", ["balanced", "turbo", "custom"])
def test_a_stale_turbo_lora_value_is_ignored_not_rejected(template, mode, profile):
    pipes = _render(template, mode, speed_profile=profile, turbo_lora=STALE_TURBO_LORA)
    assert _loras(pipes) == []
    assert STALE_TURBO_LORA not in _emitted_models(pipes)


@pytest.mark.parametrize("mode", MODES + ["inpaint", "control"])
def test_no_mode_declares_a_turbo_lora_field(mode):
    tab = (PRESET_DIR / f"modes/{mode}/tabs/generation.yml").read_text(encoding="utf-8")
    pipeline = (PRESET_DIR / f"modes/{mode}/pipeline.yml").read_text(encoding="utf-8")
    assert "turbo_lora" not in tab and "turbo_lora" not in pipeline


@pytest.mark.parametrize("mode", MODES + ["inpaint"])
def test_the_turbo_option_describes_the_turbo_model(mode):
    tab = yaml.safe_load((PRESET_DIR / f"modes/{mode}/tabs/generation.yml").read_text(encoding="utf-8"))
    speed = next(f for f in tab["fields"] if f.get("name") == "speed_profile")
    assert {"label": "Turbo", "value": "turbo", "sub_label": "8 steps, for the Turbo model"} in speed["configuration"]["options"]


def test_txt2img_recommends_the_pinned_turbo_checkpoint():
    tab = yaml.safe_load((PRESET_DIR / "modes/txt2img/tabs/generation.yml").read_text(encoding="utf-8"))
    field = next(c for s in tab["fields"] for c in s.get("children", []) if c.get("name") == "diffusion_model")
    rec = next(r for r in field["configuration"]["recommendations"] if "turbo" in r["name"])
    assert rec["sha256"] == TURBO_SHA256
    assert rec["link"].endswith("/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors")
