from pathlib import Path

import yaml

from src.pipelines.pipes.generator.flux.main import GeneratorFluxPipe

ROOT = Path(__file__).resolve().parents[3]
PRESET = ROOT / "content" / "presets" / "marketplace" / "Flux2"
EDIT = PRESET / "modes" / "edit"


def _names(node, found=None):
    found = set() if found is None else found
    if isinstance(node, dict):
        if "name" in node and "type" in node:
            found.add(node["name"])
        for value in node.values():
            _names(value, found)
    elif isinstance(node, list):
        for value in node:
            _names(value, found)
    return found


def test_edit_is_declared_and_unvalidated():
    preset = yaml.safe_load((PRESET / "preset.yml").read_text())
    assert "edit" in preset["modes"]
    assert "edit" in preset["modes_unvalidated"]


def test_references_field_is_multi_image_capped_at_the_klein_limit():
    field = yaml.safe_load((EDIT / "tabs" / "references.yml").read_text())["fields"][0]
    assert field["name"] == "source_image"
    assert field["type"] == "image"
    assert field["configuration"]["multi"] is True
    assert field["configuration"]["max_items"] == GeneratorFluxPipe.MAX_REFERENCES == 4


def test_edit_form_has_no_guidance_input():
    names = set()
    for tab in (EDIT / "tabs").glob("*.yml"):
        _names(yaml.safe_load(tab.read_text()), names)
    names |= _names(yaml.safe_load((PRESET / "modes" / "img2img" / "tabs" / "advanced.yml").read_text()))
    assert "output_size" in names
    assert not {"cfg", "guidance", "denoise"} & names


def test_pipeline_runs_the_flux_generator_in_edit_mode_over_every_reference():
    pipeline = yaml.safe_load((EDIT / "pipeline.yml").read_text())["pipeline"]
    generator = next(p for p in pipeline if p["name"] == "generator/flux")
    assert generator["configuration"]["mode"] == "edit"
    assert generator["configuration"]["output_size"] == "{{ form.output_size }}"
    loader = next(p for p in pipeline if p["name"] == "media_loader")
    assert loader["configuration"]["media"]["@loop"]["items"] == "{{ form.source_image }}"
