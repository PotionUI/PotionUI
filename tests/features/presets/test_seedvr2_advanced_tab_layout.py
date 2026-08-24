"""Both SeedVR2 upscale modes group their Advanced-tab fields into named
sections -- the same `section` idiom Krea-2/Wan/LTX-2.5/Anima use -- rather
than the pre-idiom `group` field type. Output Size, Restoration Intent and
VAE Decode Tiling are common to both `upscale` (image) and `video_upscale`
modes; `video_upscale` additionally carries Temporal Batching and Audio
sections that have no image-mode counterpart.

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

COMMON_FIELD_NAMES = {
    "scale",
    "target_short_side",
    "latent_noise_scale",
    "input_noise_scale",
    "color_correction",
    "tile_size",
    "tile_overlap",
}

EXPECTED_FIELD_NAMES = {
    "upscale": COMMON_FIELD_NAMES,
    "video_upscale": COMMON_FIELD_NAMES
    | {
        "batch_size",
        "temporal_overlap",
        "prepend_frames",
        "uniform_batch_size",
        "keep_audio",
    },
}

EXPECTED_TOP_LEVEL_TITLES = {
    "upscale": [
        "Output Size (Expert Override)",
        "Restoration Intent (Expert Override)",
        "Color Correction",
        "VAE Decode Tiling",
    ],
    "video_upscale": [
        "Output Size (Expert Override)",
        "Restoration Intent (Expert Override)",
        "Color Correction",
        "Temporal Batching",
        "Audio",
        "VAE Decode Tiling",
    ],
}


@pytest.fixture(scope="module")
def loader():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings_manager=Mock()))


def _advanced_tab(loader, serializer, mode: str):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith("presets/marketplace/SeedVR2")]
    assert len(matches) == 1, f"SeedVR2 matched {len(matches)} presets"
    preset = matches[0]
    schema = serializer.process_form_fields(preset.modes[mode].forms[0], preset.id)
    tabs = schema["properties"]["tabs"]["children"]
    titles = [t.get("title") for t in tabs]
    assert "Advanced" in titles, f"SeedVR2 {mode} tabs are {titles}"
    return tabs[titles.index("Advanced")]


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


@pytest.mark.parametrize("mode", ["upscale", "video_upscale"])
def test_advanced_tab_keeps_every_field_name(loader, serializer, mode):
    """Pure regrouping: field names are the pipeline.yml Jinja contract."""
    tab = _advanced_tab(loader, serializer, mode)
    assert _all_field_names(tab) == EXPECTED_FIELD_NAMES[mode]


@pytest.mark.parametrize("mode", ["upscale", "video_upscale"])
def test_advanced_tab_top_level_sections_replace_groups(loader, serializer, mode):
    tab = _advanced_tab(loader, serializer, mode)
    top_level = tab["children"]
    titles = [c.get("title") for c in top_level]
    assert titles == EXPECTED_TOP_LEVEL_TITLES[mode]
    section_titles = set(EXPECTED_TOP_LEVEL_TITLES[mode]) - {"Color Correction"}
    section_types = [c.get("type") for c in top_level if c.get("title") in section_titles]
    assert section_types == ["section"] * len(section_types)
