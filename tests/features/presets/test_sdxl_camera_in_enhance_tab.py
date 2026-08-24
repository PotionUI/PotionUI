"""The SDXL txt2img preset no longer ships a standalone "Camera" tab -- the
`camera` field moved onto the "Enhance" tab as its own "Camera" section,
alongside the tab's Effects/Upscaler sections and the Tiled-Detailer gate.
The field's name, type, and configuration (label, vocabulary overrides) are
unchanged; only its tab placement moved.

The assertions run through the same path that serves `GET /api/presets/{id}/form`
(PresetTemplateLoader -> PresetFormSerializer.process_form_fields) -- a tab
body arrives as a `children:` Jinja path string and only becomes fields inside
the serializer's `_resolve_external_children`.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.templating.processor import TemplateProcessor


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings_manager=Mock()))


@pytest.fixture(scope="module")
def txt2img_tabs(loader, serializer):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/SDXL")]
    assert len(matches) == 1, f"SDXL matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes["txt2img"].forms[0], preset.id)
    return schema["properties"]["tabs"]["children"]


def _find_field(node, name):
    if node.get("name") == name:
        return node
    for child in node.get("children") or []:
        found = _find_field(child, name)
        if found is not None:
            return found
    return None


def test_camera_tab_is_gone(txt2img_tabs):
    titles = [t.get("title") for t in txt2img_tabs]
    assert "Camera" not in titles, f"SDXL txt2img tabs are {titles}"


def test_enhance_tab_carries_a_camera_section(txt2img_tabs):
    titles = [t.get("title") for t in txt2img_tabs]
    assert "Enhance" in titles, f"SDXL txt2img tabs are {titles}"
    enhance = txt2img_tabs[titles.index("Enhance")]
    section_titles = [c.get("title") for c in enhance["children"] if c.get("type") == "section"]
    assert "Camera" in section_titles


def _shot_phrase(catalog, key):
    for category in catalog:
        for shot in category["shots"]:
            if shot["key"] == key:
                return shot["phrase"]
    raise AssertionError(f"shot '{key}' not found in catalog")


def test_camera_field_keeps_its_type_label_and_vocabulary(txt2img_tabs):
    titles = [t.get("title") for t in txt2img_tabs]
    enhance = txt2img_tabs[titles.index("Enhance")]
    camera = _find_field(enhance, "camera")
    assert camera is not None, "camera field did not survive the move to Enhance"
    assert camera.get("type") == "camera_shot"
    assert camera.get("title") == "Camera & Shot"
    assert _shot_phrase(camera["catalog"], "overhead") == "from directly above, top-down view"
    assert _shot_phrase(camera["catalog"], "low_angle") == "low angle shot, looking up at the subject"
