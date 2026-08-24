"""Layout for the native LTX-2 preset's face/hand detailer and NAG blocks
(video mode's Enhance tab, and the standalone upscale mode's Generation tab
"Enhancement" section): the genuine enable-boolean (`enhance_faces_hands`,
which governs `enhancement_strength`/`face_detector_model`/`hand_detector_model`
via `reactions` before this rework) takes the `gate` form, matching the family
convention landed on SDXL/Wan the same day. NAG has no enable boolean of its
own (`nag_scale` at 1.0 means off, not a checkbox) so its header/container
takes `section`, not `gate`. `upscale` stays a plain `select` -- it is a
three-state control (off/1.5x/2.0x), not a boolean, so it cannot become a gate
(same reasoning as Wan's CFG-Zero* staying a section: a gate would hide a
control that isn't a governed sibling of a single boolean).

The assertions run through the same path that serves `GET /api/presets/{id}/form`
(PresetTemplateLoader -> PresetFormSerializer.process_form_fields) since a tab
body arrives as a `children:` Jinja path string and only becomes fields inside
the serializer's `_resolve_external_children` -- a hand-built fixture or a raw
`yaml.safe_load` of the tab file would assert against a tree the frontend never
receives.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.templating.processor import TemplateProcessor

DETAILER_FIELD_NAMES = {"enhancement_strength", "face_detector_model", "hand_detector_model"}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings_manager=Mock()))


@pytest.fixture(scope="module")
def preset(loader):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/LTX-2")]
    assert len(matches) == 1, f"LTX-2 matched {len(matches)} presets"
    return matches[0]


def _tab(serializer, preset, mode, label):
    schema = serializer.process_form_fields(preset.modes[mode].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert label in titles, f"LTX-2 {mode} tabs are {titles}"
    return tabs[titles.index(label)]


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


@pytest.fixture(scope="module")
def enhance_tab(serializer, preset):
    return _tab(serializer, preset, "video", "Enhance")


@pytest.fixture(scope="module")
def upscale_generation_tab(serializer, preset):
    return _tab(serializer, preset, "upscale", "Generation")


# -- video mode: Enhance tab --------------------------------------------------

def test_enhance_tab_keeps_every_field_name(enhance_tab):
    """Pure regrouping / gate conversion: field names are the pipeline.yml Jinja contract."""
    expected = {
        "upscale", "refine_strength", "upscale_model", "enhancement_loras",
        "nag_scale", "nag_tau", "nag_alpha",
        "enhance_faces_hands", *DETAILER_FIELD_NAMES,
    }
    assert _all_field_names(enhance_tab) == expected


def test_enhance_tab_nag_is_a_section_not_a_gate(enhance_tab):
    nag = next(c for c in enhance_tab["children"] if c.get("title") == "Negative prompt guidance (NAG)")
    assert nag["type"] == "section"
    assert nag.get("name") is None


def test_enhance_tab_upscale_stays_a_plain_select(enhance_tab):
    upscale = next(c for c in enhance_tab["children"] if c.get("name") == "upscale")
    assert upscale["type"] == "select"


def test_enhance_tab_detailer_is_a_gate_owning_its_own_boolean(enhance_tab):
    gate = next(c for c in enhance_tab["children"] if c.get("name") == "enhance_faces_hands")
    assert gate["type"] == "gate"
    assert gate["default"] is False
    assert _all_field_names(gate) == DETAILER_FIELD_NAMES


def test_enhance_tab_detailer_gate_has_no_redundant_reactions(enhance_tab):
    gate = next(c for c in enhance_tab["children"] if c.get("name") == "enhance_faces_hands")
    for child in gate["children"]:
        assert not child.get("reactions")


# -- standalone upscale mode: Generation tab's "Enhancement" section ---------

def test_upscale_generation_enhancement_section_keeps_every_field_name(upscale_generation_tab):
    enhancement = next(c for c in upscale_generation_tab["children"] if c.get("title") == "Enhancement")
    assert enhancement["type"] == "section"
    expected = {"refine_strength", "enhance_faces_hands", *DETAILER_FIELD_NAMES}
    assert _all_field_names(enhancement) == expected


def test_upscale_generation_detailer_is_a_gate_owning_its_own_boolean(upscale_generation_tab):
    enhancement = next(c for c in upscale_generation_tab["children"] if c.get("title") == "Enhancement")
    gate = next(c for c in enhancement["children"] if c.get("name") == "enhance_faces_hands")
    assert gate["type"] == "gate"
    assert gate["default"] is False
    assert _all_field_names(gate) == DETAILER_FIELD_NAMES


def test_upscale_generation_detailer_gate_has_no_redundant_reactions(upscale_generation_tab):
    enhancement = next(c for c in upscale_generation_tab["children"] if c.get("title") == "Enhancement")
    gate = next(c for c in enhancement["children"] if c.get("name") == "enhance_faces_hands")
    for child in gate["children"]:
        assert not child.get("reactions")
