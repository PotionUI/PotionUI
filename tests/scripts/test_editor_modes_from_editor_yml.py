import copy
from pathlib import Path

import pytest
import yaml

from scripts import editor_modes_from_editor_yml as converter

ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = ROOT / "content" / "presets" / "marketplace"

SURROUNDINGS = {
    "into": {"tab": "advanced"},
    "fields": [
        {
            "name": "region_context",
            "type": "select",
            "label": "Surroundings",
            "audience": "advanced",
            "default": "0.5",
            "ai_hint": "How much of the picture around the marked area the model sees. Wide helps the change match large neighbouring shapes.",
            "configuration": {
                "options": [
                    {"label": "Tight", "value": "0.25"},
                    {"label": "Balanced", "value": "0.5"},
                    {"label": "Wide", "value": "1.0"},
                ]
            },
        }
    ],
}

HAND_CORRECTIONS = {
    ("Flux3", "edit"): {"fields": {"add": [SURROUNDINGS]}},
    ("Krea2", "edit"): {
        "fixed": {"match_source": True, "edit_task": "edit", "fit_mode": "fit", "ref_position": "aligned"},
        "fields": {
            "hide": ["source_image_b", "ref_position_a", "ref_boost_a"],
            "change": {
                "resolution": {
                    "label": "Detail",
                    "ai_hint": "The pixel budget the marked area is rendered at. The result keeps the area's shape.",
                }
            },
        },
    },
    ("MiniMax-H3-Image", "edit"): {"fields": {"hide": ["resolution"]}},
    ("QwenImage-2.1", "edit"): {"region": "crop", "inputs": {"source": "source_image"}},
    ("QwenImage", "edit"): {"fields": {"hide": ["resolution", "output_fit"]}},
    ("MOSS-Image", "inpaint"): {},
    ("QwenImage-2.1", "inpaint"): {},
    ("SDXL", "inpaint"): {},
    ("ZImage", "inpaint"): {},
}


def converted(preset, mode):
    source = MARKETPLACE / preset / "modes" / mode / "editor.yml"
    (conversion,) = converter.convert_file(source)
    return conversion


def committed(preset, mode):
    path = MARKETPLACE / preset / "editor_modes" / mode / "editor_mode.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("preset, mode", sorted(HAND_CORRECTIONS))
def test_the_committed_editor_mode_is_the_converted_file_plus_the_hand_corrections(preset, mode):
    conversion = converted(preset, mode)
    assert conversion.name == mode
    assert conversion.target == MARKETPLACE / preset / "editor_modes" / mode / "editor_mode.yml"
    expected = {**copy.deepcopy(conversion.data), **copy.deepcopy(HAND_CORRECTIONS[(preset, mode)])}
    assert committed(preset, mode) == expected


@pytest.mark.parametrize("preset, mode", sorted(key for key, value in HAND_CORRECTIONS.items() if value))
def test_every_hand_correction_really_differs_from_the_converted_file(preset, mode):
    conversion = converted(preset, mode)
    for key, value in HAND_CORRECTIONS[(preset, mode)].items():
        assert conversion.data.get(key) != value


def test_the_converter_maps_every_key_of_a_declaration(tmp_path):
    task = {
        "task": "edit_region",
        "region": "crop",
        "label": "Patch",
        "inputs": {"image": {"field": "pictures", "index": 1}},
        "set": {"output_size": "match"},
        "owned": ["seed", "output_size", "pictures", "extra"],
        "seed_field": "seed",
        "quantity_field": "count",
        "size": {"area": 524288, "multiple": 8, "field": "resolution"},
        "context": 1.0,
    }
    document = converter.convert_task("edit", task)
    assert document == {
        "schema_version": 1,
        "task": "edit_region",
        "from": "edit",
        "label": "Patch",
        "region": "crop",
        "inputs": {"source": {"field": "pictures", "index": 1}, "quantity": "count", "seed": "seed"},
        "size": {"area": 524288, "multiple": 8, "field": "resolution"},
        "context": 1.0,
        "fixed": {"output_size": "match"},
        "fields": {"hide": ["extra", "resolution"]},
    }


def test_a_declaration_with_several_tasks_makes_one_editor_mode_per_task(tmp_path):
    source = tmp_path / "Preset" / "modes" / "edit" / "editor.yml"
    source.parent.mkdir(parents=True)
    source.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "tasks": [
                    {"task": "edit_region", "region": "crop", "inputs": {"image": "source_image"}},
                    {"task": "acme.relight", "region": "mask", "inputs": {"image": "source_image", "mask": "source_image_inpaint_mask"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    names = [conversion.name for conversion in converter.convert_file(source)]
    assert names == ["edit-edit-region", "edit-acme-relight"]


def test_the_default_run_only_prints_and_never_writes(tmp_path, capsys):
    preset = tmp_path / "Preset"
    mode = preset / "modes" / "edit"
    mode.mkdir(parents=True)
    (mode / "editor.yml").write_text(
        "schema_version: 1\ntasks:\n  - task: edit_region\n    region: crop\n    inputs:\n      image: source_image\n",
        encoding="utf-8",
    )
    assert converter.main([str(preset)]) == 0
    assert "WOULD WRITE" in capsys.readouterr().out
    assert not (preset / "editor_modes").exists()


def test_write_creates_the_file_and_never_touches_the_declaration_or_an_existing_target(tmp_path, capsys):
    preset = tmp_path / "Preset"
    mode = preset / "modes" / "edit"
    mode.mkdir(parents=True)
    declaration = mode / "editor.yml"
    text = "schema_version: 1\ntasks:\n  - task: edit_region\n    region: crop\n    inputs:\n      image: source_image\n"
    declaration.write_text(text, encoding="utf-8")
    assert converter.main([str(preset), "--write"]) == 0
    target = preset / "editor_modes" / "edit" / "editor_mode.yml"
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["from"] == "edit"
    assert declaration.read_text(encoding="utf-8") == text
    target.write_text("hand edited\n", encoding="utf-8")
    capsys.readouterr()
    assert converter.main([str(preset), "--write"]) == 0
    assert "SKIP" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == "hand edited\n"
    assert declaration.read_text(encoding="utf-8") == text


def test_a_tree_without_declarations_converts_nothing(tmp_path, capsys):
    assert converter.main([str(tmp_path)]) == 0
    assert "No modes/<mode>/editor.yml found" in capsys.readouterr().out
