from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.presets import operations
from src.features.presets.file_repository import FilePresetRepository
from src.features.presets.linter import PresetLinter
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.mode_labels import (
    MODE_ICONS,
    MODE_LABELS,
    fallback_mode_label,
    known_icon_names,
    mode_display_icon,
    mode_display_name,
)
from src.features.presets.routes import PresetController, build_router
from src.platform.security.current_user import get_current_active_user

ROOT = Path(__file__).resolve().parents[3]
PRESET_ID = "01TESTMODELABELS000000000"

PRESET_YML = f"""schema: 1
id: "{PRESET_ID}"
name: "Labels"
version: "1.0.0"
category: "image"
engine: "native"
modes:
  - txt2img
  - refs
  - face_swap
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _preset(root, refs_form='name: "custom"\nfields: []\n', face_form='name: "custom"\nfields: []\n'):
    preset = root / "Labels"
    _write(preset / "preset.yml", PRESET_YML)
    _write(preset / "tests.yml", "schema: 1\ncases: []\n")
    forms = {"txt2img": 'name: "custom"\nfields: []\n', "refs": refs_form, "face_swap": face_form}
    for mode, form in forms.items():
        _write(preset / "modes" / mode / "form.yml", form)
        _write(preset / "modes" / mode / "pipeline.yml", "pipeline: []\n")
    return preset


def _served(root, field):
    collaborators = SimpleNamespace(file_repo=FilePresetRepository(PresetTemplateLoader([str(root)])))
    result = operations.get_available_modes(collaborators, PRESET_ID)
    return {m["name"]: m[field] for m in result["modes"]}


def _served_labels(root):
    return _served(root, "label")


@pytest.mark.parametrize("key, label", [
    ("txt2img", "Text to Image"),
    ("img2img", "Image to Image"),
    ("txt2video", "Text to Video"),
    ("t2v", "Text to Video"),
    ("img2vid", "Image to Video"),
    ("i2v", "Image to Video"),
    ("vid2vid", "Video to Video"),
    ("txt2music", "Text to Music"),
    ("img2mesh", "Image to 3D"),
    ("video_upscale", "Video Upscale"),
    ("TXT2IMG", "Text to Image"),
])
def test_map_names_well_known_keys(key, label):
    assert mode_display_name(key) == label


@pytest.mark.parametrize("key, label", [
    ("face_swap", "Face Swap"),
    ("style-transfer", "Style Transfer"),
    ("relight", "Relight"),
])
def test_unknown_keys_fall_back_to_title_case(key, label):
    assert mode_display_name(key) == label
    assert fallback_mode_label(key) == label


def test_override_beats_the_map():
    assert mode_display_name("refs", "References to Video") == "References to Video"
    assert mode_display_name("refs", "   ") == MODE_LABELS["refs"]


def test_every_shipped_mode_key_has_a_plain_name():
    keys = set()
    for base in ("content/presets/marketplace", "content/plugins/marketplace"):
        for preset_yml in (ROOT / base).rglob("preset.yml"):
            keys.update((yaml.safe_load(preset_yml.read_text(encoding="utf-8")) or {}).get("modes") or [])
    assert keys
    assert sorted(k for k in keys if k.lower() not in MODE_LABELS) == []


def test_modes_endpoint_serves_labels_with_override_and_fallback(tmp_path):
    _preset(tmp_path, refs_form='name: "custom"\nmode_label: "References to Video"\nfields: []\n')
    assert _served_labels(tmp_path) == {
        "txt2img": "Text to Image",
        "refs": "References to Video",
        "face_swap": "Face Swap",
    }


def test_override_on_a_variant_form_is_not_the_mode_label(tmp_path):
    preset = _preset(tmp_path)
    _write(preset / "modes" / "refs" / "variants" / "alt" / "form.yml", 'name: "alt"\nmode_label: "Nope"\nfields: []\n')
    assert _served_labels(tmp_path)["refs"] == "References"


def test_lint_hints_only_for_keys_that_fall_back(tmp_path):
    _preset(tmp_path)
    hits = [i for i in PresetLinter([str(tmp_path)]).lint() if i.level == "info" and "mode_label" in i.message]
    assert [h.message for h in hits] == [
        'modes/face_swap: no plain name for this mode key, so it shows as "Face Swap"; add mode_label to its form.yml'
    ]


def test_lint_quiet_when_a_fallback_key_has_mode_label(tmp_path):
    _preset(tmp_path, face_form='name: "custom"\nmode_label: "Swap a Face"\nfields: []\n')
    assert not any("mode_label" in i.message for i in PresetLinter([str(tmp_path)]).lint())


def test_mode_labels_route_serves_the_map_ahead_of_preset_ids():
    app = FastAPI()
    app.include_router(build_router(SimpleNamespace(preset_controller=PresetController(Mock(), Mock()))))
    app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(id="u1")
    response = TestClient(app).get("/api/presets/mode-labels")
    assert response.status_code == 200
    assert response.json()["data"]["labels"] == MODE_LABELS


@pytest.mark.parametrize("key, icon", [
    ("txt2img", "image"),
    ("img2img", "layers"),
    ("txt2vid", "video"),
    ("img2video", "video"),
    ("inpaint", "brush"),
    ("upscale", "expand"),
    ("edit", "pencil"),
    ("control", "sliders"),
    ("txt2music", "audio"),
    ("img2mesh", "cube"),
])
def test_map_gives_well_known_keys_an_icon(key, icon):
    assert mode_display_icon(key) == icon


def test_icon_override_beats_the_map_and_unknown_keys_have_none():
    assert mode_display_icon("txt2img", "wand") == "wand"
    assert mode_display_icon("txt2img", " ") == "image"
    assert mode_display_icon("face_swap") is None


def test_every_mapped_icon_is_in_the_icon_set():
    icons = known_icon_names()
    assert icons and "image" in icons and "chevron-down" in icons
    assert sorted(set(MODE_ICONS.values()) - icons) == []


def test_icon_set_is_unknown_without_the_frontend_source(tmp_path):
    assert known_icon_names(tmp_path / "IconLibrary.ts") is None


def test_modes_endpoint_serves_icons_with_override_and_none(tmp_path):
    _preset(tmp_path, refs_form='name: "custom"\nmode_icon: "film"\nfields: []\n')
    assert _served(tmp_path, "icon") == {"txt2img": "image", "refs": "film", "face_swap": None}


def test_lint_warns_on_an_unknown_mode_icon(tmp_path):
    _preset(tmp_path, refs_form='name: "custom"\nmode_icon: "not-an-icon"\nfields: []\n',
            face_form='name: "custom"\nmode_label: "Swap a Face"\nmode_icon: "face"\nfields: []\n')
    hits = [i for i in PresetLinter([str(tmp_path)]).lint() if "mode_icon" in i.message]
    assert [(h.level, h.message) for h in hits] == [
        ("warning", 'modes/refs: mode_icon "not-an-icon" is not an icon in the app\'s Icon set')
    ]
