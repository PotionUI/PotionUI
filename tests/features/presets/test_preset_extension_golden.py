import json

import pytest

from scripts.preset_render import (
    ROOT,
    build_processor,
    golden_filename,
    load_all_presets,
    render_preset_mode,
)
from src.features.presets.extensions import walk_fields
from src.features.presets.linter import PresetLinter

FIXTURE_PLUGIN = ROOT / "tests" / "e2e" / "plugins" / "preset-extension"
GOLDEN_DIR = ROOT / "tests" / "golden" / "preset_extensions"
KREA2_ID = "4TK1KBQZ2XMB8ME0PTMXS1YJQP"
CORE_GOLDEN = ROOT / "tests" / "golden" / "preset_renders" / f"{KREA2_ID}__txt2img.json"


@pytest.fixture(scope="module")
def extended():
    presets, load_errors = load_all_presets(
        presets_root=ROOT / "content" / "presets" / "marketplace", plugin_dirs=[FIXTURE_PLUGIN]
    )
    return next(p for p in presets if p.id == KREA2_ID), load_errors


def test_fixture_extension_applies_to_the_core_preset(extended):
    preset, load_errors = extended
    assert {k: v for k, v in load_errors.items() if "preset_extensions" in k} == {}
    form = preset.modes["txt2img"].forms[0]
    grain = next(f for f, _ in walk_fields(form.fields) if f.name == "grain_intensity")
    assert grain.source_plugin == "e2e-preset-extension"
    advanced = next(f for f, _ in walk_fields(form.fields) if f.type == "tab" and f.anchor == "advanced")
    section_names = [f.name for f, _ in walk_fields(advanced.children)]
    assert section_names.index("grain_intensity") == section_names.index("cfg") + 1


def test_merged_pipeline_matches_the_golden_render(extended):
    preset, _ = extended
    record = render_preset_mode(build_processor(), preset, "txt2img")
    expected = json.loads((GOLDEN_DIR / golden_filename(preset, "txt2img")).read_text())
    assert record == expected


def test_golden_differs_from_core_only_by_the_extension():
    extended_pipes = json.loads((GOLDEN_DIR / f"{KREA2_ID}__txt2img.json").read_text())["pipes"]
    core_pipes = json.loads(CORE_GOLDEN.read_text())["pipes"]
    without_grain = [p for p in extended_pipes if p["id"] != "grain"]
    generator = next(p for p in without_grain if p["id"] == "generator")
    assert generator["config"].pop("detail_strength") == {"type": "float", "value": 0.0}
    assert without_grain == core_pipes
    grain = next(p for p in extended_pipes if p["id"] == "grain")
    assert grain["name"] == "film_grain"
    assert grain["config"]["intensity"] == {"type": "float", "value": 0.3}


def test_fixture_extension_is_lint_clean_against_the_real_pipe_catalog():
    from scripts.preset_render import manifests_from_plugin_dirs

    linter = PresetLinter(
        [str(ROOT / "content" / "presets" / "marketplace" / "Krea2")],
        plugin_manifests=manifests_from_plugin_dirs([FIXTURE_PLUGIN]),
    )
    assert linter._lint_preset_extensions() == []
