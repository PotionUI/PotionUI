"""The SDXL txt2img ControlNet tab groups its fields into named sections: a
"General" section (the enable/auto-preprocess toggles) and a "ControlNet
Units" section wrapping the `@loop`-generated accordions. The loop still
expands to 3 accordions carrying the same per-slot field names; only the
presentation wrapper is new.

The assertions run through the same path that serves `GET /api/presets/{id}/form`
(PresetTemplateLoader -> PresetFormSerializer.process_form_fields), which is
also what expands the `@loop` field template -- a raw `yaml.safe_load` of the
tab file would see the unexpanded loop directive, not the 3 accordions the
frontend receives.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.templating.processor import TemplateProcessor

EXPECTED_LOOP_FIELD_NAMES = {
    f"controlnet_{i}_{suffix}"
    for i in (1, 2, 3)
    for suffix in ("model", "type", "image", "start", "end", "scale")
}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings=Mock()))


@pytest.fixture(scope="module")
def controlnet_tab(loader, serializer):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/SDXL")]
    assert len(matches) == 1, f"SDXL matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes["txt2img"].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "ControlNet" in titles, f"SDXL txt2img tabs are {titles}"
    return tabs[titles.index("ControlNet")]


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


def test_controlnet_tab_top_level_is_named_sections(controlnet_tab):
    top_level = controlnet_tab["children"]
    assert [c.get("type") for c in top_level] == ["section", "section"]
    assert [c.get("title") for c in top_level] == ["General", "ControlNet Units"]


def test_general_section_holds_the_toggles(controlnet_tab):
    general = controlnet_tab["children"][0]
    assert _all_field_names(general) == {"enable_controlnet", "controlnet_auto_preprocess"}


def test_controlnet_units_section_still_expands_to_three_accordions(controlnet_tab):
    units = controlnet_tab["children"][1]
    accordions = units["children"]
    assert [a.get("type") for a in accordions] == ["accordion", "accordion", "accordion"]
    assert [a.get("title") for a in accordions] == ["ControlNet 1", "ControlNet 2", "ControlNet 3"]
    assert _all_field_names(units) == EXPECTED_LOOP_FIELD_NAMES
