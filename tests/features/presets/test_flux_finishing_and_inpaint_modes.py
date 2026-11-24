from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.features.presets.extensions import walk_fields
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

MASK = "/uploads/source_mask.png"
MODELS = {
    "Flux1": {
        "diffusion_model": "/models/flux1_dit.safetensors",
        "text_encoder": "/models/t5xxl.safetensors",
        "clip_l": "/models/clip_l.safetensors",
        "vae": "/models/ae.safetensors",
    },
    "Flux2": {
        "diffusion_model": "/models/flux2_dit.safetensors",
        "text_encoder": "/models/qwen3.safetensors",
        "vae": "/models/flux2_vae.safetensors",
    },
}
NEW_MODES = ["inpaint", "upscale", "refine_faces"]
FAMILIES = sorted(MODELS)


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    found = {}
    for name in FAMILIES:
        template = next((p for p in loader.presets if f"marketplace/{name}" in Path(p.path).as_posix()), None)
        if template is None:
            pytest.fail(f"marketplace/{name} preset not present")
        found[name] = template
    return found


def _form(family, mode, over=None):
    form = dict(MODELS[family])
    if mode != "txt2img":
        form["source_image"] = "/uploads/source.png"
    if mode == "inpaint":
        form["source_image_inpaint_mask"] = MASK
    if mode == "refine_faces":
        form["face_model"] = "/models/face_landmarker.task"
    if over:
        form.update(over)
    return form


def _process(templates, family, mode, over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    template = templates[family]
    form_data = bound_form_data(template, mode, _form(family, mode, over))
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _providers(pipes, consumer):
    return {i["name"]: i["provider"] for i in _pipe(pipes, consumer)["input"]}


@pytest.mark.parametrize("family", FAMILIES)
def test_the_new_modes_are_declared_and_flagged_unvalidated(templates, family):
    template = templates[family]
    extra = ["edit"] if family == "Flux2" else []
    assert list(template.modes) == ["txt2img", "img2img", *extra, *NEW_MODES]
    assert set(template.modes_unvalidated) == set(extra) | set(NEW_MODES)


@pytest.mark.parametrize("family", FAMILIES)
def test_the_editor_modes_ride_on_the_new_generation_modes(templates, family):
    editor = templates[family].editor_modes
    assert editor["expand"].editor.from_mode == "inpaint"
    assert editor["upscale"].editor.from_mode == "upscale"
    assert editor["fix_faces"].editor.from_mode == "refine_faces"


@pytest.mark.parametrize("family", FAMILIES)
def test_inpaint_runs_the_flux_generator_in_inpaint_mode_on_the_cropped_region(templates, family):
    pipes = _process(templates, family, "inpaint", {"mask_grow": 9, "mask_feather": 12, "denoise": 0.7})
    cfg = _pipe(pipes, "generator/flux")["config"]
    assert cfg["mode"] == "inpaint"
    assert float(cfg["denoise"]) == 0.7
    inputs = {i["name"]: i for i in _pipe(pipes, "generator/flux")["input"]}
    assert inputs["image"]["provider"] == "inpaint_region_crop" and inputs["image"]["output_var"] == "images"
    assert inputs["mask"]["provider"] == "inpaint_region_crop" and inputs["mask"]["output_var"] == "masks"
    assert _pipe(pipes, "mask_loader")["config"]["media"][0]["path"] == MASK
    assert int(_pipe(pipes, "mask_preprocessor")["config"]["dilate_iterations"]) == 9
    restore = _pipe(pipes, "inpaint_region_restore")
    assert restore["enabled"] is True and float(restore["config"]["feather"]) == pytest.approx(0.12)
    assert _providers(pipes, "gallery")["image"] == "inpaint_region_restore"


@pytest.mark.parametrize("family", FAMILIES)
def test_inpaint_defaults_to_a_full_repaint(templates, family):
    cfg = _pipe(_process(templates, family, "inpaint"), "generator/flux")["config"]
    assert float(cfg["denoise"]) == 1.0


@pytest.mark.parametrize("family", FAMILIES)
def test_upscale_refines_tile_by_tile_with_the_loaded_model(templates, family):
    pipes = _process(templates, family, "upscale", {"upscale_factor": 3.0, "refine_amount": "strong", "tile_size": 768})
    cfg = _pipe(pipes, "tiled_refiner")["config"]
    assert float(cfg["scale"]) == 3.0
    assert float(cfg["denoise"]) == 0.45
    assert int(cfg["tile_size"]) == 768
    providers = _providers(pipes, "tiled_refiner")
    assert providers["model"] == "model_loader/flux" and providers["image"] == "media_loader"
    assert _providers(pipes, "gallery")["image"] == "tiled_refiner"


@pytest.mark.parametrize("family", FAMILIES)
def test_refine_faces_repaints_faces_and_can_take_hands_along(templates, family):
    over = {"fix_strength": 0.5, "face_detailer_hands": True, "face_detailer_hand_model": "/models/hand_landmarker.task"}
    pipes = _process(templates, family, "refine_faces", over)
    cfg = _pipe(pipes, "face_detailer")["config"]
    assert float(cfg["denoise"]) == 0.5
    assert cfg["face_model"] == "/models/face_landmarker.task"
    assert cfg["detect_hands"] is True and cfg["hand_model"] == "/models/hand_landmarker.task"
    assert _providers(pipes, "face_detailer")["model"] == "model_loader/flux"
    assert _providers(pipes, "gallery")["image"] == "face_detailer"


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("mode", ["txt2img", "img2img", *NEW_MODES])
def test_every_mode_offers_up_to_ten_loras_without_step_windows(templates, family, mode):
    configs = [
        field.configuration
        for form in templates[family].modes[mode].forms
        for field, _ in walk_fields(form.fields)
        if field.type == "lora_picker" and field.name == "loras"
    ]
    assert len(configs) == 1
    config = configs[0]
    assert config["max_items"] == 10
    assert (config["strength_min"], config["strength_max"]) == (-10.0, 10.0)
    assert not config.get("row_fields")
