from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

MASK = "/uploads/source_mask.png"
FAMILIES = {
    "ZImage": {
        "generator": "generator/z_image",
        "models": {
            "diffusion_model": "/models/zimage_dit.safetensors",
            "text_encoder": "/models/zimage_te.safetensors",
            "vae": "/models/zimage_vae.safetensors",
        },
    },
}
INPAINT_FAMILIES = sorted(FAMILIES)


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


def _form(family, over=None, mask=True):
    form = dict(FAMILIES[family]["models"])
    form["source_image"] = "/uploads/source.png"
    if mask:
        form["source_image_inpaint_mask"] = MASK
    if over:
        form.update(over)
    return form


def _process(templates, family, mode, over=None, mask=True):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    template = templates[family]
    form_data = bound_form_data(template, mode, _form(family, over, mask))
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form_data})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _providers(pipes, consumer):
    return {i["name"]: i["provider"] for i in _pipe(pipes, consumer)["input"]}


def _generator(templates, family, pipes):
    return _pipe(pipes, FAMILIES[family]["generator"])["config"]


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_inpaint_is_a_declared_mode(templates, family):
    assert "inpaint" in templates[family].modes


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_inpaint_runs_the_generator_in_inpaint_mode_on_the_cropped_region(templates, family):
    pipes = _process(templates, family, "inpaint")
    cfg = _generator(templates, family, pipes)
    assert cfg["mode"] == "inpaint"
    providers = _providers(pipes, FAMILIES[family]["generator"])
    assert providers["image"] == "inpaint_region_crop"
    assert providers["mask"] == "inpaint_region_crop"
    assert _pipe(pipes, "mask_loader")["config"]["media"][0]["path"] == MASK
    assert _pipe(pipes, "inpaint_region_crop")["config"]["strategy"] == "scaled"
    restore = _pipe(pipes, "inpaint_region_restore")
    assert restore["enabled"] is True and restore["config"]["strategy"] == "composite"
    assert _providers(pipes, "gallery")["image"] == "inpaint_region_restore"


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_grow_feather_and_strength_reach_the_pipes(templates, family):
    pipes = _process(templates, family, "inpaint", {"mask_grow": 9, "mask_feather": 12, "denoise": 0.7})
    assert int(_pipe(pipes, "mask_preprocessor")["config"]["dilate_iterations"]) == 9
    assert float(_pipe(pipes, "inpaint_region_restore")["config"]["feather"]) == pytest.approx(0.12)
    assert float(_generator(templates, family, pipes)["denoise"]) == 0.7


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_inpaint_defaults_to_a_full_repaint(templates, family):
    assert float(_generator(templates, family, _process(templates, family, "inpaint"))["denoise"]) == 1.0


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_inpaint_needs_a_painted_area(templates, family):
    with pytest.raises(FormBindingError) as refused:
        bind_form(
            templates[family], "inpaint", form_name=None, raw_form_data=_form(family, mask=False),
            user_id=None, storage_dir=None,
        )
    assert "source_image" in refused.value.field_errors


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_a_blank_prompt_falls_back_to_continuing_the_surroundings(templates, family):
    pipes = _process(templates, family, "inpaint")
    encoder = _pipe(pipes, "prompt_encoder")["config"]
    assert encoder["p_prompt"]["input"].strip()
    assert "surroundings" in encoder["p_prompt"]["input"]


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_the_prompt_is_optional_and_labelled(templates, family):
    template = templates[family]
    assert "inpaint" in template.vars["optional_prompt_modes"]
    copy = template.vars["prompt"]["modes"]["inpaint"]
    assert copy["label"] == "What should be there instead"
    assert len(copy["label"]) <= 32


def test_zimage_outpaint_grows_the_canvas_by_pixels(templates):
    pipes = _process(
        templates, "ZImage", "outpaint",
        {"expand_left": 128, "expand_right": 64, "expand_top": 32, "expand_bottom": 0}, mask=False,
    )
    cfg = _pipe(pipes, "canvas_expand")["config"]
    assert [int(cfg[k]) for k in ("left", "right", "top", "bottom")] == [128, 64, 32, 0]
    assert cfg["aspect"] == ""
    assert _providers(pipes, "canvas_expand")["image"] == "media_loader"


def test_zimage_outpaint_grows_to_a_shape_and_ignores_the_sides(templates):
    pipes = _process(
        templates, "ZImage", "outpaint",
        {"canvas_mode": "shape", "target_shape": "21:9", "shape_anchor": "left", "expand_left": 500}, mask=False,
    )
    cfg = _pipe(pipes, "canvas_expand")["config"]
    assert cfg["aspect"] == "21:9" and cfg["anchor"] == "left"
    assert int(cfg["left"]) == 0


def test_zimage_outpaint_repaints_only_the_new_canvas(templates):
    pipes = _process(templates, "ZImage", "outpaint", mask=False)
    generator = _generator(templates, "ZImage", pipes)
    providers = _providers(pipes, "generator/z_image")
    assert generator["mode"] == "inpaint"
    assert providers["image"] == "canvas_expand" and providers["mask"] == "canvas_expand"
    assert _providers(pipes, "gallery")["image"] == "generator/z_image"
    assert all(p["name"] != "inpaint_region_crop" for p in pipes)


@pytest.mark.parametrize("size,resolution", [("1", "1024x1024"), ("2", "1448x1448"), ("4", "2048x2048")])
def test_zimage_outpaint_output_size_sets_the_working_area(templates, size, resolution):
    pipes = _process(templates, "ZImage", "outpaint", {"output_size": size}, mask=False)
    assert _generator(templates, "ZImage", pipes)["resolution"] == resolution


def test_zimage_outpaint_needs_only_a_picture(templates):
    bind_form(
        templates["ZImage"], "outpaint", form_name=None, raw_form_data=_form("ZImage", mask=False),
        user_id=None, storage_dir=None,
    )
    with pytest.raises(FormBindingError):
        bind_form(
            templates["ZImage"], "outpaint", form_name=None, raw_form_data=dict(FAMILIES["ZImage"]["models"]),
            user_id=None, storage_dir=None,
        )
