from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.preset_render import build_fixture_form_data
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

NEW_MODES = ["inpaint", "refine_faces"]


@pytest.fixture(scope="module")
def krea2():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/Krea2")), None)
    if template is None:
        pytest.fail("marketplace/Krea2 preset not present")
    return template


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


def test_new_modes_are_listed_and_flagged_unvalidated(krea2):
    assert all(mode in krea2.modes for mode in NEW_MODES)
    assert krea2.modes_unvalidated == NEW_MODES


def test_inpaint_repaints_through_the_krea2_generator_with_the_cropped_region(krea2):
    pipes = _pipes(krea2, "inpaint")
    generator = _pipe(pipes, "generator/krea2")
    assert generator["config"]["mode"] == "inpaint"
    assert ("mask", "inpaint_region_crop", "masks") in _wired(generator)
    assert ("image", "inpaint_region_crop", "images") in _wired(generator)
    assert _pipe(pipes, "inpaint_region_restore")["enabled"]


def test_inpaint_strength_reaches_the_generator(krea2):
    assert _pipe(_pipes(krea2, "inpaint", {"denoise": 0.6}), "generator/krea2")["config"]["denoise"] == 0.6


def test_fix_faces_runs_only_the_detailer_on_the_source_image(krea2):
    pipes = _pipes(krea2, "refine_faces", {"fix_strength": 0.5})
    assert not any(p["name"].startswith("generator/") for p in pipes)
    detailer = _pipe(pipes, "detailer/native")
    assert detailer["config"]["denoise"] == 0.5
    assert ("image", "media_loader", "image") in _wired(detailer)
    assert ("image", "face_detailer", "image") in _wired(_pipe(pipes, "gallery"))


def test_fix_faces_leaves_hands_off_until_asked(krea2):
    assert _pipe(_pipes(krea2, "refine_faces"), "detailer/native")["config"]["detect_hands"] is False


def test_fix_faces_passes_the_hand_choice_and_model_to_the_detailer(krea2):
    config = _pipe(
        _pipes(krea2, "refine_faces", {"face_detailer_hands": True, "face_detailer_hand_model": "/m/hand.task"}),
        "detailer/native",
    )["config"]
    assert config["detect_hands"] is True
    assert config["hand_model"] == "/m/hand.task"


def test_the_generation_page_face_detailer_passes_the_hand_choice_too(krea2):
    pipes = _pipes(
        krea2,
        "txt2img",
        {"face_detailer_enabled": True, "face_detailer_hands": True, "face_detailer_hand_model": "/m/hand.task"},
    )
    config = _pipe(pipes, "detailer/native")["config"]
    assert (config["detect_hands"], config["hand_model"]) == (True, "/m/hand.task")


def test_editor_modes_derive_from_the_new_generation_modes(krea2):
    derived = {name: (mode.editor.task, mode.editor.from_mode) for name, mode in krea2.editor_modes.items()}
    assert derived["expand"] == ("expand", "inpaint")
    assert derived["fix_faces"] == ("fix_faces", "refine_faces")
    assert derived["upscale"] == ("upscale", "upscale")
