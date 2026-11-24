from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from ._bound_form import bound_form_data

ROOT = Path("content/presets/marketplace")
PICKED = [
    {"model": "/models/loras/style.safetensors", "strength": 0.7},
    {"model": "/models/loras/off.safetensors", "strength": 0},
]
ANIMA_MODES = ("txt2img", "img2img", "upscale", "refine_faces")
ANIMA_FORM = {
    "diffusion_model": "/models/anima.safetensors",
    "text_encoder": "/models/qwen3_06b.safetensors",
    "vae": "/models/qwen_image_vae.safetensors",
    "source_image": "/media/a.png",
}
FLUX3_FORM = {
    "diffusion_model": "/models/flux3.safetensors",
    "text_encoder": "/models/qwen3vl.safetensors",
    "vae": "/models/ae.safetensors",
    "source_image": ["/media/a.png"],
}


@pytest.fixture(scope="module")
def templates():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    return {
        name: next(p for p in loader.presets if f"marketplace/{name}" in Path(p.path).as_posix())
        for name in ("Anima", "Flux3")
    }


def _process(template, mode, over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()), settings=Mock(), preset_template_loader=Mock(),
    )
    form = bound_form_data(template, mode, over)
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": form})


def _pipe(pipes, name):
    return next(p for p in pipes if p.get("id") == name or p["name"] == name)


def _links(pipe):
    return [(link["name"], link["provider"], link["output_var"]) for link in pipe["input"]]


def _manifest(name):
    return yaml.safe_load((ROOT / name / "preset.yml").read_text(encoding="utf-8"))


def test_anima_lists_every_new_mode_as_unvalidated():
    manifest = _manifest("Anima")
    assert manifest["modes"] == list(ANIMA_MODES)
    assert manifest["modes_unvalidated"] == ["img2img", "upscale", "refine_faces"]


def test_flux3_keeps_its_two_modes_and_marks_none_unvalidated():
    manifest = _manifest("Flux3")
    assert manifest["modes"] == ["txt2img", "edit"]
    assert not manifest.get("modes_unvalidated")


@pytest.mark.parametrize("mode", ANIMA_MODES)
def test_anima_lora_tab_uses_the_shared_fragment(mode):
    form = yaml.safe_load((ROOT / "Anima" / "modes" / mode / "form.yml").read_text(encoding="utf-8"))
    tabs = {tab["anchor"]: tab for tab in form["fields"][0]["children"]}
    assert tabs["lora"]["children"]["file"] == "{{ paths._shared }}/native/lora.yml"
    assert tabs["lora"]["children"]["vars"]["max_items"] == 10


@pytest.mark.parametrize("mode", ["txt2img", "edit"])
def test_flux3_lora_tab_uses_the_shared_fragment(mode):
    form = yaml.safe_load((ROOT / "Flux3" / "modes" / mode / "form.yml").read_text(encoding="utf-8"))
    tabs = {tab["anchor"]: tab for tab in form["fields"][0]["children"]}
    assert tabs["lora"]["children"]["file"] == "{{ paths._shared }}/native/lora.yml"
    assert _manifest("Flux3")["configuration"]["lora_tags"]["type"] == "model_tags"


@pytest.mark.parametrize("mode", ANIMA_MODES)
def test_anima_picked_loras_reach_the_loader(templates, mode):
    pipes = _process(templates["Anima"], mode, {**ANIMA_FORM, "loras": PICKED})
    loras = _pipe(pipes, "model_loader/anima")["config"]["loras"]
    assert [(l["file_path"], float(l["weight"])) for l in loras] == [
        ("/models/loras/style.safetensors", 0.7),
        ("/models/loras/off.safetensors", 0.0),
    ]


@pytest.mark.parametrize("mode", ["txt2img", "edit"])
def test_flux3_picked_loras_reach_the_loader(templates, mode):
    pipes = _process(templates["Flux3"], mode, {**FLUX3_FORM, "loras": PICKED})
    loras = _pipe(pipes, "model_loader/flux3")["config"]["loras"]
    assert [(l["file_path"], float(l["weight"])) for l in loras] == [
        ("/models/loras/style.safetensors", 0.7),
        ("/models/loras/off.safetensors", 0.0),
    ]
    rows = _pipe(pipes, "param_emitter")["config"]["parameters"]
    recorded = [pair[1] for row in rows if row for pair in (row if isinstance(row[0], list) else [row]) if pair[0] == "model"]
    assert "/models/loras/style.safetensors" in recorded
    assert "/models/loras/off.safetensors" not in recorded


def test_anima_img2img_runs_the_generator_on_the_source_image(templates):
    pipes = _process(templates["Anima"], "img2img", {**ANIMA_FORM, "denoise": 0.4})
    generator = _pipe(pipes, "generator/anima")
    assert generator["config"]["mode"] == "img2img"
    assert generator["config"]["denoise"] == pytest.approx(0.4)
    assert "resolution" not in generator["config"]
    assert ("image", "media_loader", "image") in _links(generator)
    assert _pipe(pipes, "media_loader")["config"]["media"][0]["path"] == "/media/a.png"


def test_anima_upscale_refines_tile_by_tile_on_the_loaded_model(templates):
    pipes = _process(templates["Anima"], "upscale", {**ANIMA_FORM, "upscale_factor": "3"})
    refiner = _pipe(pipes, "tiled_refiner")
    assert refiner["config"]["scale"] == pytest.approx(3.0)
    assert ("model", "model_loader/anima", "model") in _links(refiner)
    assert ("image", "tiled_refiner", "image") in _links(_pipe(pipes, "gallery"))


def test_anima_refine_faces_runs_the_native_detailer_with_the_form_strength(templates):
    pipes = _process(
        templates["Anima"], "refine_faces",
        {**ANIMA_FORM, "fix_strength": 0.45, "fix_max_faces": 2, "fix_face_model": "/models/face_landmarker.task"},
    )
    detailer = _pipe(pipes, "face_detailer")
    assert detailer["name"] == "detailer/native"
    assert detailer["config"]["denoise"] == pytest.approx(0.45)
    assert detailer["config"]["max_faces"] == 2
    assert detailer["config"]["face_model"] == "/models/face_landmarker.task"
    assert ("model", "model_loader/anima", "model") in _links(detailer)
    assert ("image", "face_detailer", "image") in _links(_pipe(pipes, "gallery"))
