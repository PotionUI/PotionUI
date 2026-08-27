"""The SDXL txt2img and inpaint Advanced tabs group their fields into a
"Sampling" section (steps, cfg, clip_skip, then the sampler/scheduler row),
the same idiom Krea-2/Anima/Flux use on their own Advanced tabs, followed by
the ADM Guidance and SAG feature blocks, each rendered as a `gate` field
(the collapsible-with-real-boolean idiom, matching the face/hand/eyes/teeth/
person detailer gates on the txt2img tab).

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

EXPECTED_FIELD_NAMES = {
    "steps",
    "cfg",
    "clip_skip",
    "sampler",
    "scheduler",
    "adm_guidance_enabled",
    "adm_positive_scale",
    "adm_negative_scale",
    "adm_scaler_end",
    "sag_enabled",
    "sag_scale",
    "sag_sigma",
    "sag_threshold",
}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings=Mock()))


def _sdxl_advanced_tab(loader, serializer, mode):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/SDXL")]
    assert len(matches) == 1, f"SDXL matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes[mode].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "Advanced" in titles, f"SDXL {mode} tabs are {titles}"
    return tabs[titles.index("Advanced")]


@pytest.fixture(scope="module")
def advanced_tab(loader, serializer):
    return _sdxl_advanced_tab(loader, serializer, "txt2img")


@pytest.fixture(scope="module")
def inpaint_advanced_tab(loader, serializer):
    return _sdxl_advanced_tab(loader, serializer, "inpaint")


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


def test_advanced_tab_keeps_every_field_name(advanced_tab):
    """Pure regrouping: field names are the pipeline.yml Jinja contract."""
    assert _all_field_names(advanced_tab) == EXPECTED_FIELD_NAMES


def test_advanced_tab_top_level_is_named_sections(advanced_tab):
    top_level = advanced_tab["children"]
    assert [c.get("type") for c in top_level] == ["section", "gate", "gate"]
    assert [c.get("title") for c in top_level] == [
        "Sampling",
        "Adaptive Diffusion Model (ADM) Guidance",
        "Self-Attention Guidance (SAG)",
    ]
    assert [c.get("name") for c in top_level] == [None, "adm_guidance_enabled", "sag_enabled"]


def test_sampling_section_orders_steps_cfg_clip_skip_then_sampler_scheduler(advanced_tab):
    sampling = advanced_tab["children"][0]
    assert _all_field_names(sampling) == {"steps", "cfg", "clip_skip", "sampler", "scheduler"}
    assert [c.get("name") for c in sampling["children"][:3]] == ["steps", "cfg", "clip_skip"]


def test_inpaint_advanced_tab_keeps_every_field_name(inpaint_advanced_tab):
    """Pure regrouping: field names are the pipeline.yml Jinja contract."""
    assert _all_field_names(inpaint_advanced_tab) == EXPECTED_FIELD_NAMES


def test_inpaint_advanced_tab_top_level_is_named_sections(inpaint_advanced_tab):
    top_level = inpaint_advanced_tab["children"]
    assert [c.get("type") for c in top_level] == ["section", "gate", "gate"]
    assert [c.get("title") for c in top_level] == [
        "Sampling",
        "Adaptive Diffusion Model (ADM) Guidance",
        "Self-Attention Guidance (SAG)",
    ]
    assert [c.get("name") for c in top_level] == [None, "adm_guidance_enabled", "sag_enabled"]


def test_inpaint_sampling_section_orders_steps_cfg_clip_skip_then_sampler_scheduler(inpaint_advanced_tab):
    sampling = inpaint_advanced_tab["children"][0]
    assert _all_field_names(sampling) == {"steps", "cfg", "clip_skip", "sampler", "scheduler"}
    assert [c.get("name") for c in sampling["children"][:3]] == ["steps", "cfg", "clip_skip"]
