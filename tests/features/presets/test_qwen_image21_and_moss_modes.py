from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

NEW_MODES = ["img2img", "upscale", "refine_faces"]
FAMILIES = {
    "QwenImage-2.1": ("generator/qwen_image21", "model_loader/qwen_image21"),
    "MOSS-Image": ("generator/moss_image", "model_loader/moss_image"),
}


@pytest.fixture(scope="module")
def presets():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = {}
    for folder in FAMILIES:
        template = next((p for p in loader.presets if Path(p.path).as_posix().endswith(f"marketplace/{folder}")), None)
        if template is None:
            pytest.fail(f"marketplace/{folder} preset not present")
        found[folder] = template
    return found


def _pipes(template, mode, form_over=None):
    form_data = build_fixture_form_data(template, mode)
    if form_over:
        form_data.update(form_over)
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p["name"] == name)


def _wired(pipe):
    return {(edge["name"], edge["provider"], edge["output_var"]) for edge in pipe["input"]}


@pytest.mark.parametrize("folder", FAMILIES)
def test_new_modes_are_listed_and_flagged_unvalidated(presets, folder):
    template = presets[folder]
    assert all(mode in template.modes for mode in NEW_MODES)
    assert all(mode in template.modes_unvalidated for mode in NEW_MODES)


@pytest.mark.parametrize("folder", FAMILIES)
def test_existing_modes_are_not_flagged_unvalidated(presets, folder):
    assert "txt2img" not in presets[folder].modes_unvalidated
    assert "inpaint" not in presets[folder].modes_unvalidated


@pytest.mark.parametrize("folder", FAMILIES)
def test_img2img_denoises_the_source_picture_at_the_chosen_strength(presets, folder):
    generator_name, loader_name = FAMILIES[folder]
    generator = _pipe(_pipes(presets[folder], "img2img", {"denoise": 0.45}), generator_name)
    assert generator["config"]["mode"] == "img2img"
    assert generator["config"]["denoise"] == 0.45
    assert ("image", "media_loader", "image") in _wired(generator)
    assert ("model", loader_name, "model") in _wired(generator)


@pytest.mark.parametrize("folder", FAMILIES)
def test_upscale_runs_the_tiled_refiner_on_the_family_model(presets, folder):
    _, loader_name = FAMILIES[folder]
    pipes = _pipes(presets[folder], "upscale", {"upscale_factor": 3.0})
    assert not any(p["name"].startswith("generator/") for p in pipes)
    refiner = _pipe(pipes, "tiled_refiner")
    assert refiner["config"]["scale"] == 3.0
    assert ("model", loader_name, "model") in _wired(refiner)
    assert ("image", "tiled_refiner", "image") in _wired(_pipe(pipes, "gallery"))


@pytest.mark.parametrize("folder", FAMILIES)
def test_fix_faces_runs_only_the_detailer_and_forwards_the_hand_choice(presets, folder):
    pipes = _pipes(
        presets[folder],
        "refine_faces",
        {"fix_strength": 0.5, "face_detailer_hands": True, "face_detailer_hand_model": "/m/hand.task"},
    )
    assert not any(p["name"].startswith("generator/") for p in pipes)
    detailer = _pipe(pipes, "detailer/native")
    assert detailer["config"]["denoise"] == 0.5
    assert detailer["config"]["detect_hands"] is True
    assert detailer["config"]["hand_model"] == "/m/hand.task"
    assert ("image", "face_detailer", "image") in _wired(_pipe(pipes, "gallery"))


@pytest.mark.parametrize("folder", FAMILIES)
def test_editor_modes_derive_from_the_new_generation_modes(presets, folder):
    derived = {name: (mode.editor.task, mode.editor.from_mode) for name, mode in presets[folder].editor_modes.items()}
    assert derived["fix_faces"] == ("fix_faces", "refine_faces")
    assert derived["upscale"] == ("upscale", "upscale")


@pytest.mark.parametrize("folder", FAMILIES)
def test_every_mode_uses_the_shared_unwindowed_lora_tab(folder):
    forms = sorted((Path("content/presets/marketplace") / folder / "modes").glob("*/form.yml"))
    assert forms
    for form in forms:
        text = form.read_text(encoding="utf-8")
        assert "native/lora.yml" in text, form
        assert "lora_windowed" not in text, form
        assert "tabs/lora.yml" not in text, form
        assert "max_items: 10" in text, form
