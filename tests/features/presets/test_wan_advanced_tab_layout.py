"""The Wan video Advanced tab groups its fields into a "Sampling" section
(steps, sampler, cfg, then scheduler/manual sigmas) the same idiom Krea-2/
Anima/Flux/SDXL use on their own Advanced tabs, followed by feature blocks.
Long-video RoPE (RIFLEx) is the only block with a genuine checkbox enable --
it renders as a `gate` field (the collapsible-with-real-boolean idiom, matching
the SDXL ADM/SAG gates). The other blocks (Expert switching, NAG, CFG-Zero*,
APG, SLG, FreeInit, Step cache) switch on a param value rather than a
checkbox, so they stay plain sections.

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
    "schedule",
    "manual_sigmas",
    "expert_boundary_preset",
    "expert_switch_step",
    "nag_scale",
    "nag_tau",
    "nag_alpha",
    "cfg_zero_star",
    "zero_init_steps",
    "apg_eta",
    "apg_norm_threshold",
    "apg_momentum",
    "slg_scale",
    "slg_layers",
    "slg_sigma_start",
    "slg_sigma_end",
    "freeinit_iterations",
    "freeinit_cutoff",
    "freeinit_order",
    "riflex",
    "riflex_trained_frames",
    "step_cache_threshold",
    "step_cache_warmup_steps",
    "step_cache_max_skips",
}

EXPECTED_TOP_LEVEL_TYPES = [
    "section",
    "section",
    "section",
    "section",
    "section",
    "section",
    "section",
    "gate",
    "section",
]

EXPECTED_TOP_LEVEL_TITLES = [
    "Sampling",
    "Expert switching",
    "Negative prompt guidance (NAG)",
    "CFG-Zero*",
    "Adaptive Projected Guidance (APG)",
    "Skip-Layer Guidance (SLG)",
    "FreeInit (temporal flicker) -- text-to-video only",
    "Long-video RoPE (RIFLEx)",
    "Step cache (FBCache)",
]

EXPECTED_TOP_LEVEL_NAMES = [None, None, None, None, None, None, None, "riflex", None]


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
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/Wan")]
    assert len(matches) == 1, f"Wan matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes["video"].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "Advanced" in titles, f"Wan video tabs are {titles}"
    return tabs[titles.index("Advanced")]


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


def test_advanced_tab_keeps_every_field_name(advanced_tab):
    """Pure regrouping / gate conversion: field names are the pipeline.yml Jinja contract."""
    assert _all_field_names(advanced_tab) == EXPECTED_FIELD_NAMES


def test_advanced_tab_top_level_shape(advanced_tab):
    top_level = advanced_tab["children"]
    assert [c.get("type") for c in top_level] == EXPECTED_TOP_LEVEL_TYPES
    assert [c.get("title") for c in top_level] == EXPECTED_TOP_LEVEL_TITLES
    assert [c.get("name") for c in top_level] == EXPECTED_TOP_LEVEL_NAMES


def test_sampling_section_is_first_and_orders_steps_sampler_cfg(advanced_tab):
    sampling = advanced_tab["children"][0]
    assert sampling["title"] == "Sampling"
    assert _all_field_names(sampling) == {"steps", "sampler", "cfg", "schedule", "manual_sigmas"}
    assert [c.get("name") for c in sampling["children"][:3]] == ["steps", "sampler", "cfg"]


def test_riflex_is_a_gate_owning_its_own_boolean(advanced_tab):
    riflex_gate = advanced_tab["children"][EXPECTED_TOP_LEVEL_TITLES.index("Long-video RoPE (RIFLEx)")]
    assert riflex_gate["type"] == "gate"
    assert riflex_gate["name"] == "riflex"
    assert riflex_gate["default"] is False
    assert _all_field_names(riflex_gate) == {"riflex_trained_frames"}


def test_riflex_gate_has_no_redundant_disable_reactions(advanced_tab):
    riflex_gate = advanced_tab["children"][EXPECTED_TOP_LEVEL_TITLES.index("Long-video RoPE (RIFLEx)")]
    trained_frames = riflex_gate["children"][0]
    assert trained_frames["name"] == "riflex_trained_frames"
    assert not trained_frames.get("reactions")
