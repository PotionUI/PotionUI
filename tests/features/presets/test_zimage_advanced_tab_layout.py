"""The Z-Image txt2img Advanced tab groups its fields into named sections, the
same idiom Krea-2/Anima/Flux/Flux2 use on their own Advanced tabs: a
"Sampling" section (steps/sampler/cfg, in that order, then the Z-Image
sigma-shift override), a "Step cache (FBCache)" section, and a "Spectral
Progressive Diffusion" section, all three rendered with the `section` field
type rather than a bare `row` or the `group` type the pre-rework file used.

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
    "sampler",
    "cfg",
    "shift",
    "step_cache_threshold",
    "step_cache_warmup_steps",
    "step_cache_max_skips",
    "spectral_progressive_enabled",
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
def advanced_tab(loader, serializer):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/ZImage")]
    assert len(matches) == 1, f"ZImage matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes["txt2img"].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "Advanced" in titles, f"ZImage txt2img tabs are {titles}"
    return tabs[titles.index("Advanced")]


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
    assert [c.get("type") for c in top_level] == ["section", "section", "section"]
    assert [c.get("title") for c in top_level] == [
        "Sampling",
        "Step cache (FBCache)",
        "Spectral Progressive Diffusion",
    ]


def test_sampling_section_orders_steps_sampler_cfg(advanced_tab):
    sampling = advanced_tab["children"][0]
    names_in_order = [c.get("name") for c in sampling["children"] if c.get("name")]
    assert names_in_order[:3] == ["steps", "sampler", "cfg"]
