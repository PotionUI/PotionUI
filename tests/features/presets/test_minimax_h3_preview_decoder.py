from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.features.presets import PresetTemplateLoader

PRESETS_ROOT = Path("content/presets/marketplace")
GENERATOR = "generator/video_minimax_h3"


def _previewing_generators(pipeline_file: Path):
    document = yaml.safe_load(pipeline_file.read_text(encoding="utf-8"))
    for pipe in document["pipeline"]:
        configuration = pipe.get("configuration") or {}
        if pipe.get("name") == GENERATOR and "preview" in configuration:
            yield pipe.get("id") or pipe["name"], configuration


def _cases():
    cases = []
    for preset_file in sorted(PRESETS_ROOT.glob("MiniMax-H3*/preset.yml")):
        preset_id = yaml.safe_load(preset_file.read_text(encoding="utf-8"))["id"]
        for pipeline_file in sorted(preset_file.parent.glob("modes/*/pipeline.yml")):
            generators = list(_previewing_generators(pipeline_file))
            if generators:
                mode = pipeline_file.parent.name
                cases.append(pytest.param(preset_id, mode, generators, id=f"{preset_file.parent.name}-{mode}"))
    return cases


def _field_names(fields):
    for field in fields:
        if field.name:
            yield field.name
        yield from _field_names(field.children or [])


@pytest.fixture(scope="module")
def presets():
    loader = PresetTemplateLoader([str(PRESETS_ROOT)])
    loader.load_presets()
    return {p.id: p for p in loader.presets}


def test_the_tree_has_previewing_modes():
    assert len(_cases()) >= 10


@pytest.mark.parametrize("preset_id, mode, generators", _cases())
def test_previewing_mode_exposes_and_wires_the_preview_decoder(presets, preset_id, mode, generators):
    form = presets[preset_id].modes[mode].forms[0]
    assert "preview_decoder" in set(_field_names(form.fields))
    for generator_id, configuration in generators:
        assert "form.preview_decoder" in str(configuration.get("preview_decoder")), generator_id
