from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from src.features.forms.binding import FormBindingError, bind_form
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

PRESET_DIR = Path("content/presets/marketplace/Pixal3D")
MOGE = "/models/moge_2_vitl_normal_fp16.safetensors"


@pytest.fixture(scope="module")
def pixal3d_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "marketplace/Pixal3D" in str(p.path)), None)
    if template is None:
        pytest.skip("marketplace/Pixal3D preset not present")
    return template


def _form(mode: str, **over) -> dict:
    form = {
        "diffusion_model": "/models/pixal3d.safetensors",
        "shape_vae": "/models/shape_vae.safetensors",
        "texture_vae": "/models/texture_vae.safetensors",
        "image_encoder": "/models/dino_naf.safetensors",
    }
    form["source_image" if mode == "img2mesh" else "front_image"] = "/uploads/chair.png"
    form.update(over)
    return form


def _bind(template, mode: str, **over):
    return bind_form(template, mode, form_name=None, raw_form_data=_form(mode, **over), user_id=None, storage_dir=None)


def _process(template, mode: str, **over):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    bound = _bind(template, mode, **over)
    return processor.process(template, {"prompts": [], "mode": mode, "form_data": dict(bound.values)})


def _config(pipes, name):
    return next(p for p in pipes if p["name"] == name)["config"]


def test_image_to_mesh_defaults_to_auto_fov_like_comfyuis_workflow(pixal3d_template):
    bound = _bind(pixal3d_template, "img2mesh", camera_estimator=MOGE)
    assert bound.values["camera_fov_mode"] == "auto"


def test_views_to_mesh_defaults_to_the_rigs_manual_20_degrees(pixal3d_template):
    bound = _bind(pixal3d_template, "views2mesh")
    assert bound.values["camera_fov_mode"] == "manual"
    assert float(bound.values["camera_fov"]) == 20.0


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_auto_loads_the_picked_estimator_and_asks_the_generator_to_estimate(pixal3d_template, mode):
    pipes = _process(pixal3d_template, mode, camera_fov_mode="auto", camera_estimator=MOGE)
    assert _config(pipes, "model_loader/pixal3d")["camera_estimator"]["file_path"] == MOGE
    assert _config(pipes, "generator/pixal3d")["camera_fov_mode"] == "auto"


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_manual_never_loads_the_estimator_even_when_one_is_picked(pixal3d_template, mode):
    pipes = _process(pixal3d_template, mode, camera_fov_mode="manual", camera_fov=33.0, camera_estimator=MOGE)
    assert _config(pipes, "model_loader/pixal3d")["camera_estimator"]["file_path"] == ""
    generator = _config(pipes, "generator/pixal3d")
    assert generator["camera_fov_mode"] == "manual"
    assert float(generator["camera_fov"]) == 33.0


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_auto_without_an_estimator_is_refused_at_submission(pixal3d_template, mode):
    with pytest.raises(FormBindingError) as refused:
        _bind(pixal3d_template, mode, camera_fov_mode="auto")
    assert "camera_estimator" in refused.value.field_errors


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_manual_runs_need_no_estimator(pixal3d_template, mode):
    bound = _bind(pixal3d_template, mode, camera_fov_mode="manual")
    assert bound.values["camera_fov_mode"] == "manual"


def _fields(mode: str) -> list:
    found = []

    def walk(items):
        for item in items or []:
            if isinstance(item, dict):
                found.append(item)
                children = item.get("children")
                if isinstance(children, list):
                    walk(children)

    for tab in sorted((PRESET_DIR / "modes" / mode / "tabs").glob("*.yml")):
        walk(yaml.safe_load(tab.read_text())["fields"])
    return found


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_the_fov_slider_and_estimator_follow_the_mode_select(mode):
    fields = {field.get("name"): field for field in _fields(mode)}
    slider = fields["camera_fov"]["reactions"]
    estimator = fields["camera_estimator"]
    assert slider == [{"when": {"field": "camera_fov_mode", "equals": "auto"}, "then": {"set_visibility": False}}]
    assert estimator["reactions"] == [
        {"when": {"field": "camera_fov_mode", "not_equals": "auto"}, "then": {"set_visibility": False}}
    ]
    assert estimator["configuration"]["model_type"] == "geometry_estimation"
    assert [option["value"] for option in fields["camera_fov_mode"]["configuration"]["options"]] in (
        ["auto", "manual"], ["manual", "auto"])


@pytest.mark.parametrize("mode", ["img2mesh", "views2mesh"])
def test_pixal3d_fields_carry_no_description_lines(mode):
    assert [field.get("name") for field in _fields(mode) if "description" in field] == []
