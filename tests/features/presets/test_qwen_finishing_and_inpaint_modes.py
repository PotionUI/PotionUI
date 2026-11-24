from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data


@pytest.fixture(scope="module")
def template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = next((p for p in loader.presets if Path(p.path).as_posix().endswith("marketplace/QwenImage")), None)
    if found is None:
        pytest.fail("marketplace/QwenImage preset not present")
    return found


_MODELS = {
    "diffusion_model": "/models/qwen_dit.safetensors",
    "text_encoder": "/models/qwen_te.safetensors",
    "vae": "/models/qwen_vae.safetensors",
    "source_image": "/uploads/source.png",
}


def _process(template, mode, over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form = dict(_MODELS)
    if mode == "refine_faces":
        form["face_model"] = "/models/face_landmarker.task"
    if over:
        form.update(over)
    form_data = bound_form_data(template, mode, form)
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _providers(pipes, consumer):
    return {i["name"]: i["provider"] for i in _pipe(pipes, consumer)["input"]}


def test_the_new_modes_are_declared_and_flagged_unvalidated(template):
    assert list(template.modes.keys()) == ["txt2img", "img2img", "edit", "camera", "real", "inpaint", "upscale", "refine_faces"]
    assert set(template.modes_unvalidated) == {"inpaint", "upscale", "refine_faces"}


def test_the_new_modes_carry_the_matching_editor_modes(template):
    assert template.editor_modes["expand"].editor.from_mode == "inpaint"
    assert template.editor_modes["upscale"].editor.from_mode == "upscale"
    assert template.editor_modes["fix_faces"].editor.from_mode == "refine_faces"


def test_inpaint_repaints_the_painted_area_without_re_encoding_the_picture_into_the_prompt(template):
    over = {"source_image_inpaint_mask": "/uploads/mask.png", "denoise": 0.8, "mask_feather": 20}
    pipes = _process(template, "inpaint", over)
    cfg = _pipe(pipes, "generator/qwen")["config"]
    assert cfg["mode"] == "inpaint"
    assert float(cfg["denoise"]) == 0.8
    assert float(cfg["mask_feather"]) == 0.2
    assert _pipe(pipes, "mask_preprocessor")["enabled"]
    providers = _providers(pipes, "generator/qwen")
    assert providers["image"] == "inpaint_region_crop"
    assert providers["mask"] == "inpaint_region_crop"
    assert "image" not in _providers(pipes, "prompt_encoder")
    assert _providers(pipes, "gallery")["image"] == "inpaint_region_restore"


def test_inpaint_without_a_painted_area_is_rejected(template):
    with pytest.raises(FormBindingError):
        _process(template, "inpaint")


def test_upscale_tiles_the_refine_at_the_chosen_scale_and_detail(template):
    pipes = _process(template, "upscale", {"upscale_factor": 3.0, "refine_amount": "strong", "tile_size": 768})
    cfg = _pipe(pipes, "tiled_refiner")["config"]
    assert float(cfg["scale"]) == 3.0
    assert float(cfg["denoise"]) == 0.45
    assert int(cfg["tile_size"]) == 768
    assert int(cfg["steps"]) == 20
    assert float(cfg["guidance"]) == 4.0
    providers = _providers(pipes, "tiled_refiner")
    assert providers["model"] == "model_loader/qwen"
    assert providers["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "tiled_refiner"


def test_refine_faces_repaints_faces_and_can_take_hands_along(template):
    pipes = _process(template, "refine_faces", {"fix_strength": 0.5})
    cfg = _pipe(pipes, "face_detailer")["config"]
    assert float(cfg["denoise"]) == 0.5
    assert cfg["face_model"] == "/models/face_landmarker.task"
    assert not cfg["detect_hands"]
    assert _providers(pipes, "face_detailer")["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "face_detailer"

    over = {"face_detailer_hands": True, "face_detailer_hand_model": "/models/hand_landmarker.task"}
    cfg = _pipe(_process(template, "refine_faces", over), "face_detailer")["config"]
    assert cfg["detect_hands"] is True
    assert cfg["hand_model"] == "/models/hand_landmarker.task"
