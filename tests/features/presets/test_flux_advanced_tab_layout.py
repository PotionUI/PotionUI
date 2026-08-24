"""The Flux1/Flux2 txt2img Advanced tabs group their fields into named
sections, the same idiom Krea-2/Anima/QwenImage use on their own Advanced
tabs: a "Sampling" section (steps/sampler/guidance -- Flux's cfg-equivalent is
the embedded distilled guidance scale, not a real CFG value -- in that order,
then shift and iterate_mode), and a "Step cache (FBCache)" section, all
rendered with the `section` field type rather than the `group` type the
pre-rework file used. Flux2 additionally carries a "Spectral Progressive
Diffusion" section and a `shift` field with a default; Flux1 has neither --
Flux1 uses its own dynamic-mu shift schedule and Spectral Progressive
Diffusion is silently ignored on that architecture.

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

FLUX2_FIELD_NAMES = {
    "steps",
    "sampler",
    "guidance",
    "shift",
    "iterate_mode",
    "step_cache_threshold",
    "step_cache_warmup_steps",
    "step_cache_max_skips",
    "spectral_progressive_enabled",
    "spectral_progressive_start_scale",
}

FLUX1_FIELD_NAMES = {
    "steps",
    "sampler",
    "guidance",
    "iterate_mode",
    "step_cache_threshold",
    "step_cache_warmup_steps",
    "step_cache_max_skips",
}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings_manager=Mock()))


def _advanced_tab(loader, serializer, suffix):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith(suffix)]
    assert len(matches) == 1, f"{suffix} matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes["txt2img"].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "Advanced" in titles, f"{suffix} txt2img tabs are {titles}"
    return tabs[titles.index("Advanced")]


@pytest.fixture(scope="module")
def flux2_advanced_tab(loader, serializer):
    return _advanced_tab(loader, serializer, "presets/marketplace/Flux2")


@pytest.fixture(scope="module")
def flux1_advanced_tab(loader, serializer):
    return _advanced_tab(loader, serializer, "presets/marketplace/Flux1")


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


def test_flux2_advanced_tab_keeps_every_field_name(flux2_advanced_tab):
    """Pure regrouping: field names are the pipeline.yml Jinja contract."""
    assert _all_field_names(flux2_advanced_tab) == FLUX2_FIELD_NAMES


def test_flux2_advanced_tab_top_level_is_named_sections(flux2_advanced_tab):
    top_level = flux2_advanced_tab["children"]
    assert [c.get("type") for c in top_level] == ["section", "section", "section"]
    assert [c.get("title") for c in top_level] == [
        "Sampling",
        "Step cache (FBCache)",
        "Spectral Progressive Diffusion",
    ]


def test_flux2_sampling_section_orders_steps_sampler_guidance(flux2_advanced_tab):
    sampling = flux2_advanced_tab["children"][0]
    names_in_order = [c.get("name") for c in sampling["children"] if c.get("name")]
    assert names_in_order[:3] == ["steps", "sampler", "guidance"]


def test_flux1_advanced_tab_keeps_every_field_name(flux1_advanced_tab):
    assert _all_field_names(flux1_advanced_tab) == FLUX1_FIELD_NAMES


def test_flux1_advanced_tab_top_level_is_named_sections(flux1_advanced_tab):
    """Flux1 has no Spectral Progressive Diffusion section -- that knob is
    silently ignored on the Flux1 architecture, so it isn't offered."""
    top_level = flux1_advanced_tab["children"]
    assert [c.get("type") for c in top_level] == ["section", "section"]
    assert [c.get("title") for c in top_level] == [
        "Sampling",
        "Step cache (FBCache)",
    ]


def test_flux1_sampling_section_orders_steps_sampler_guidance(flux1_advanced_tab):
    sampling = flux1_advanced_tab["children"][0]
    names_in_order = [c.get("name") for c in sampling["children"] if c.get("name")]
    assert names_in_order[:3] == ["steps", "sampler", "guidance"]
