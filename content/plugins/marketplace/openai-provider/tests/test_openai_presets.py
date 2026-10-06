import re
from pathlib import Path

import pytest
import yaml

from src.plugin_api.cloud import CLOUD_BLOCKS

from backend.catalog import catalog_specs, load_catalog
from backend.config import OpenAIConfig

PLUGIN = Path(__file__).resolve().parents[1]
PRESET = PLUGIN / "presets" / "ImageGeneration" / "standard"
MODES = {"txt2img": "txt2img", "edit": "img_edit"}


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def tabs_of(mode):
    form = load(PRESET / "modes" / mode / "form.yml")
    return next(field for field in form["fields"] if field["type"] == "tabs")["children"]


def cloud_pipe(mode):
    return next(pipe for pipe in load(PRESET / "modes" / mode / "pipeline.yml")["pipeline"] if pipe["name"] == "cloud_generate")


def test_the_preset_runs_on_the_openai_driver_with_both_modes():
    preset = load(PRESET / "preset.yml")

    assert preset["engine"] == "cloud" and preset["driver"] == OpenAIConfig.model_fields["driver"].default
    assert preset["modes"] == ["txt2img", "edit"]


@pytest.mark.parametrize("mode", list(MODES))
def test_every_tab_is_an_icon_tab(mode):
    tabs = tabs_of(mode)

    assert all(tab["configuration"]["icon_display"] == "icon_only" and tab["configuration"]["icon"] for tab in tabs)
    assert tabs[0]["label"] == "Generation" and tabs[-1]["label"] == "Provider options"


def test_edit_has_a_references_tab_and_text_to_image_has_none():
    assert [tab["label"] for tab in tabs_of("edit")] == ["Generation", "References", "Provider options"]
    assert [tab["label"] for tab in tabs_of("txt2img")] == ["Generation", "Provider options"]


@pytest.mark.parametrize("mode,task", list(MODES.items()))
def test_each_mode_names_its_task_literally(mode, task):
    assert cloud_pipe(mode)["configuration"]["task"] == task


def test_the_painted_mask_reaches_the_provider_as_the_mask_role():
    pipe = cloud_pipe("edit")
    loaders = {pipe["id"]: pipe for pipe in load(PRESET / "modes" / "edit" / "pipeline.yml")["pipeline"] if pipe["name"] == "media_loader"}

    assert pipe["configuration"]["roles"] == {"first_frame": "mask"}
    assert ["first_frame", "mask_loader", "image"] in pipe["input"]
    assert "image_inpaint_mask" in loaders["mask_loader"]["enabled"]


def test_the_picture_to_edit_takes_a_mask_and_the_references_leave_room_for_it():
    fields = {field["name"]: field for field in load(PRESET / "blocks" / "references_edit.yml")["fields"]}
    limit = next(media.max_items for media in catalog_specs(load_catalog())[0].inputs if media.role == "reference")

    assert fields["image"]["required"] is True and fields["image"]["configuration"]["allow_inpaint"] is True
    assert fields["references"]["configuration"]["max_items"] == limit - 1
    assert all(field["capability"] == {"model_field": "model", "input": "reference"} for field in fields.values())


def test_the_presets_reference_only_real_shared_blocks():
    referenced = set()
    for form in PRESET.rglob("*.yml"):
        referenced.update(re.findall(r"paths\._shared \}\}/cloud/([\w/]+\.yml)", form.read_text(encoding="utf-8")))

    assert referenced and referenced <= set(CLOUD_BLOCKS)


def test_the_manifest_registers_the_provider_the_presets_and_the_guide():
    manifest = load(PLUGIN / "manifest.yml")

    assert manifest["presets"] == [{"path": "presets"}]
    assert manifest["hooks"]["backend"][0] == {"hook": "backend.register", "handler": "backend.hooks.backend_hooks.register_backend"}
    assert manifest["type"] == "backend-only"
    assert manifest["docs"][0]["path"] == "README.md" and (PLUGIN / "README.md").is_file()


def test_setup_walks_from_backend_to_assigned_presets_on_this_driver():
    setup = load(PLUGIN / "manifest.yml")["setup"]
    driver = OpenAIConfig.model_fields["driver"].default

    assert [step["kind"] for step in setup] == ["backend.added", "cloud.models_enabled", "presets.installed", "presets.assigned"]
    assert {step["driver"] for step in setup if "driver" in step} == {driver}
