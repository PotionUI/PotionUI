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
    "MOSS-Image": {
        "generator": "generator/moss_image",
        "models": {
            "diffusion_model": "/models/moss_dit.safetensors",
            "text_encoder": "/models/moss_te.safetensors",
            "vae": "/models/moss_vae.safetensors",
        },
    },
    "QwenImage-2.1": {
        "generator": "generator/qwen_image21",
        "models": {
            "diffusion_model": "/models/qwen21_dit.safetensors",
            "text_encoder": "/models/qwen21_te.safetensors",
            "vae": "/models/qwen21_vae.safetensors",
        },
    },
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
    inputs = {i["name"]: i for i in _pipe(pipes, FAMILIES[family]["generator"])["input"]}
    assert providers["image"] == "inpaint_region_crop" and inputs["image"]["output_var"] == "images"
    assert providers["mask"] == "inpaint_region_crop" and inputs["mask"]["output_var"] == "masks"
    assert _pipe(pipes, "mask_loader")["config"]["media"][0]["path"] == MASK
    assert _pipe(pipes, "inpaint_region_crop")["config"]["strategy"] == "scaled"
    restore = _pipe(pipes, "inpaint_region_restore")
    assert restore["enabled"] is True and restore["config"]["strategy"] == "inpaint"
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














def test_qwen_turbo_profile_loads_the_turbo_lora(templates):
    pipes = _process(
        templates, "QwenImage-2.1", "inpaint",
        {"speed_profile": "turbo", "turbo_lora": "/models/loras/turbo8.safetensors"},
    )
    loras = _pipe(pipes, "model_loader/qwen_image21")["config"]["loras"]
    assert [entry["file_path"] for entry in loras] == ["/models/loras/turbo8.safetensors"]
    plain = _process(templates, "QwenImage-2.1", "inpaint")
    assert not _pipe(plain, "model_loader/qwen_image21")["config"]["loras"]


def test_moss_inpaint_keeps_the_layer_skipped_negative_branch(templates):
    pipes = _process(templates, "MOSS-Image", "inpaint")
    options = _generator(templates, "MOSS-Image", pipes)["guidance_options"]
    assert options["uncond_skip_layers"] == "22" and options["cfg_zero_star"] is False


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_the_render_harness_binds_inpaint_with_a_placeholder_mask(templates, family):
    from scripts.preset_render import build_fixture_form_data

    values = build_fixture_form_data(templates[family], "inpaint")
    assert values["source_image_inpaint_mask"].endswith("source_image_inpaint_mask.png")


def test_the_render_harness_reraises_errors_that_are_not_about_a_mask(templates, monkeypatch):
    import scripts.preset_render as harness
    from src.features.presets import fixture_form

    def refuse(*args, **kwargs):
        raise FormBindingError(["diffusion_model: bad"], {"diffusion_model": ["bad"]})

    calls = []
    monkeypatch.setattr(fixture_form, "bind_form", lambda *a, **k: calls.append(k["raw_form_data"]) or refuse())
    with pytest.raises(FormBindingError):
        harness.build_fixture_form_data(templates["ZImage"], "inpaint")
    assert len(calls) == 2


@pytest.mark.parametrize("family", INPAINT_FAMILIES)
def test_the_generator_gets_the_same_soft_edge_as_the_restore(templates, family):
    pipes = _process(templates, family, "inpaint", {"mask_feather": 20})
    assert float(_generator(templates, family, pipes)["mask_feather"]) == pytest.approx(0.2)
    assert float(_pipe(pipes, "inpaint_region_restore")["config"]["feather"]) == pytest.approx(0.2)



