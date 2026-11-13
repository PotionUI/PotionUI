from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

PRESET = "marketplace/MiniMax-H3-Image"
PRESET_DIR = Path("content/presets") / PRESET
LOADER = "model_loader/minimax_h3_image"
MODES = ("txt2img", "edit")
PICKED = [
    {"model": "/models/loras/style.safetensors", "strength": 0.7},
    {"model": "/models/loras/off.safetensors", "strength": 0},
]


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if PRESET in Path(p.path).as_posix()), None)
    if found is None:
        pytest.fail(f"{PRESET} preset not present")
    return found


def _form(over: dict) -> dict:
    form = {
        "diffusion_model": "/models/minimax_h3_image_pruned_int8_convrot.safetensors",
        "text_encoder": "/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "vae": "/models/minimax_h3_image_vae_fp16.safetensors",
        "references": ["/media/a.png"],
    }
    form.update(over)
    return form


def _process(template, mode: str, form: dict):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _recorded_models(pipes):
    rows = _pipe(pipes, "param_emitter")["config"]["parameters"]
    flat = [pair for row in rows if row for pair in (row if isinstance(row[0], list) else [row])]
    return [value for key, value in flat if key == "model"]


@pytest.mark.parametrize("mode", MODES)
def test_picked_loras_reach_the_loader_with_their_strengths(template, mode):
    form = bound_form_data(template, mode, _form({"loras": PICKED}))
    loras = _pipe(_process(template, mode, form), LOADER)["config"]["loras"]
    assert [(l["file_path"], float(l["weight"])) for l in loras] == [
        ("/models/loras/style.safetensors", 0.7),
        ("/models/loras/off.safetensors", 0.0),
    ]


@pytest.mark.parametrize("mode", MODES)
def test_only_active_loras_are_recorded_for_history(template, mode):
    form = bound_form_data(template, mode, _form({"loras": PICKED}))
    recorded = _recorded_models(_process(template, mode, form))
    assert "/models/loras/style.safetensors" in recorded
    assert "/models/loras/off.safetensors" not in recorded


@pytest.mark.parametrize("mode", MODES)
def test_no_lora_selected_leaves_an_empty_stack_and_the_same_records(template, mode):
    form = bound_form_data(template, mode, _form({}))
    pipes = _process(template, mode, form)
    assert _pipe(pipes, LOADER)["config"]["loras"] == []
    assert _recorded_models(pipes) == [
        "/models/minimax_h3_image_pruned_int8_convrot.safetensors",
        "/models/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "/models/minimax_h3_image_vae_fp16.safetensors",
    ]


@pytest.mark.parametrize("mode", MODES)
def test_a_session_saved_before_loras_existed_still_renders(template, mode):
    form = bound_form_data(template, mode, _form({}))
    form.pop("loras", None)
    pipes = _process(template, mode, form)
    assert _pipe(pipes, LOADER)["config"]["loras"] == []
    assert len(_recorded_models(pipes)) == 3


@pytest.mark.parametrize("mode", MODES)
def test_both_modes_show_a_lora_tab_before_advanced(mode):
    form = yaml.safe_load((PRESET_DIR / "modes" / mode / "form.yml").read_text(encoding="utf-8"))
    tabs = form["fields"][0]["children"]
    anchors = [tab["anchor"] for tab in tabs]
    assert anchors.index("lora") == anchors.index("advanced") - 1
    lora = tabs[anchors.index("lora")]
    assert lora["children"] == "{{ paths.preset }}/modes/txt2img/tabs/lora.yml"


def test_the_lora_picker_is_plain_and_filtered_by_the_preset_tag_setting():
    tab = yaml.safe_load((PRESET_DIR / "modes/txt2img/tabs/lora.yml").read_text(encoding="utf-8"))
    picker = tab["fields"][0]
    assert picker["type"] == "lora_picker" and picker["name"] == "loras"
    assert picker["configuration"]["filter_tags"] == "@config:lora_tags"
    assert "row_fields" not in picker["configuration"]
    preset = yaml.safe_load((PRESET_DIR / "preset.yml").read_text(encoding="utf-8"))
    assert preset["configuration"]["lora_tags"]["type"] == "model_tags"
    assert preset["formulas"]["groups"]["loras"]["label"] == "LoRAs"
