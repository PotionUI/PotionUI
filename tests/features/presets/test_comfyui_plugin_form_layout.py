"""The comfyui-backend plugin's own presets follow the same form-layout
standard as the native Krea-2 preset (`content/presets/marketplace/Krea2`),
established by the maintainer's preset tab grammar (2026-08-21) and already
enforced on core presets by `tests/features/presets/test_anima_advanced_tab_layout.py`
and `tests/features/presets/test_references_tab_layout.py`:

  - Tab order: Generation (icon "generation") -> References (icon "image",
    only for modes with conditioning media) -> LoRA (icon "lora") -> any
    family extra tabs -> Advanced (icon "settings", audience "advanced").
  - Generation tab: an optional top-level profile/select, then a section
    "Image" (seed + quantity row, then the sizing field), then a section
    "Models" holding every model picker.
  - Advanced tab: named `section`s only, "Sampling" first with steps before
    the rest, no `group`/`header` containers.

The assertions run through the same path that serves `GET /api/presets/{id}/form`
(PresetTemplateLoader -> PresetFormSerializer.process_form_fields) since a tab
body arrives as a `children:` Jinja path string and only becomes fields inside
the serializer's `_resolve_external_children` -- a hand-built fixture or a raw
`yaml.safe_load` of the tab file would assert against a tree the frontend never
receives, and would pass even if a tab body were unreachable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.plugins.loader import PluginLoader
from src.platform.templating.processor import TemplateProcessor

PLUGIN_IDS = {"comfyui-backend"}

# (preset path suffix, mode, variant or None, has References tab)
CASES = [
    ("comfyui-backend/presets/FluxKlein9b", "txt2img", None, False),
    ("comfyui-backend/presets/FluxKlein9b", "img2img", None, True),
    ("comfyui-backend/presets/Krea-2", "txt2img", None, False),
    ("comfyui-backend/presets/QwenImage", "txt2img", None, False),
    ("comfyui-backend/presets/QwenImage", "img2img", None, True),
    ("comfyui-backend/presets/SDXL", "txt2img", None, False),
    ("comfyui-backend/presets/zImage", "txt2img", None, False),
]


@pytest.fixture(scope="module")
def loader():
    manifests = [m for m in PluginLoader().discover_plugins() if m.id in PLUGIN_IDS]
    registry = SimpleNamespace(get_enabled_plugins=lambda: list(manifests))
    loader = PresetTemplateLoader(
        ["content/presets/marketplace", "content/presets/local"], plugin_registry=registry
    )
    loader.load_presets()
    return loader


@pytest.fixture(scope="module")
def serializer(loader):
    return PresetFormSerializer(loader, TemplateProcessor(settings=Mock()))


def _preset(loader, suffix):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith(suffix)]
    assert len(matches) == 1, f"{suffix} matched {len(matches)} presets"
    return matches[0]


def _tabs(loader, serializer, suffix, mode, variant):
    preset = _preset(loader, suffix)
    forms = preset.modes[mode].forms
    if variant:
        form = next(f for f in forms if getattr(f, "name", None) == variant)
    else:
        form = forms[0]
    schema = serializer.process_form_fields(form, preset.id)
    container = schema["properties"].get("tabs")
    assert container is not None, f"{suffix}/{mode}/{variant} form is not a tabs container"
    return container["children"]


def _by_title(tabs, title):
    titles = [t.get("title") for t in tabs]
    assert title in titles, f"expected a {title!r} tab, tabs are {titles}"
    return tabs[titles.index(title)]


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


def _case_id(case):
    suffix, mode, variant, _ = case
    return f"{suffix.rsplit('/', 1)[-1]}-{mode}-{variant or 'default'}"


@pytest.mark.parametrize("suffix,mode,variant,has_references", CASES, ids=[_case_id(c) for c in CASES])
def test_tab_order_and_icons(loader, serializer, suffix, mode, variant, has_references):
    tabs = _tabs(loader, serializer, suffix, mode, variant)
    titles = [t.get("title") for t in tabs]

    generation = _by_title(tabs, "Generation")
    assert generation["configuration"]["icon"] == "generation"

    advanced = _by_title(tabs, "Advanced")
    assert advanced["configuration"]["icon"] == "settings"
    assert advanced["audience"] == "advanced"
    assert titles[-1] == "Advanced", f"{suffix}/{mode}/{variant} tabs are {titles}"

    if has_references:
        ref_index = titles.index("References")
        assert titles[ref_index]
        assert titles.index("Generation") < ref_index < titles.index("LoRA")
        references = tabs[ref_index]
        assert references["configuration"]["icon"] == "image"
    else:
        assert "References" not in titles, f"{suffix}/{mode}/{variant} unexpectedly has a References tab"

    assert titles.index("Generation") < titles.index("LoRA") < titles.index("Advanced")


@pytest.mark.parametrize("suffix,mode,variant,has_references", CASES, ids=[_case_id(c) for c in CASES])
def test_generation_tab_has_image_then_models_sections(loader, serializer, suffix, mode, variant, has_references):
    generation = _by_title(_tabs(loader, serializer, suffix, mode, variant), "Generation")
    top_level = generation["children"]
    sections = [c for c in top_level if c.get("type") == "section"]
    titles = [s.get("title") for s in sections]
    assert titles == ["Image", "Models"], f"{suffix}/{mode}/{variant} Generation sections are {titles}"

    models_section = sections[titles.index("Models")]
    model_field_names = {c["name"] for c in models_section["children"] if c.get("type") == "model"}
    assert model_field_names, f"{suffix}/{mode}/{variant} Models section has no model pickers"
    # Every model field lives in the Models section, none stray elsewhere on the tab.
    assert _all_field_names(generation) & model_field_names == model_field_names


@pytest.mark.parametrize("suffix,mode,variant,has_references", CASES, ids=[_case_id(c) for c in CASES])
def test_advanced_tab_top_level_is_named_sections_sampling_first(
    loader, serializer, suffix, mode, variant, has_references
):
    advanced = _by_title(_tabs(loader, serializer, suffix, mode, variant), "Advanced")
    top_level = advanced["children"]
    # A gate is never wrapped in a section, so it is the one non-section
    # container allowed at the top level.
    assert all(c.get("type") in ("section", "gate") for c in top_level), (
        f"{suffix}/{mode}/{variant} Advanced tab has a non-section top-level child: "
        f"{[c.get('type') for c in top_level]}"
    )
    assert top_level[0].get("title") == "Sampling", (
        f"{suffix}/{mode}/{variant} Advanced tab's first section is "
        f"{top_level[0].get('title')!r}, not 'Sampling'"
    )
    sampling_names = [c.get("name") for c in top_level[0]["children"] if c.get("name")]
    assert sampling_names[0] == "steps", (
        f"{suffix}/{mode}/{variant} Sampling section doesn't start with steps: {sampling_names}"
    )


@pytest.mark.parametrize("suffix,mode,variant,has_references", CASES, ids=[_case_id(c) for c in CASES])
def test_no_group_or_header_containers_anywhere_on_the_form(
    loader, serializer, suffix, mode, variant, has_references
):
    def _walk(node, banned_types):
        for child in node.get("children") or []:
            assert child.get("type") not in banned_types, (
                f"{suffix}/{mode}/{variant} has a {child.get('type')!r} container "
                f"({child.get('name') or child.get('title')})"
            )
            _walk(child, banned_types)

    for tab in _tabs(loader, serializer, suffix, mode, variant):
        _walk(tab, {"group", "header"})


def test_qwenimage_references_tab_holds_only_media_fields(loader, serializer):
    references = _by_title(_tabs(loader, serializer, "comfyui-backend/presets/QwenImage", "img2img", None), "References")
    assert _all_field_names(references) == {"source_image", "ref_image_2", "ref_image_3"}


def test_fluxklein9b_references_tab_holds_only_media_fields(loader, serializer):
    references = _by_title(_tabs(loader, serializer, "comfyui-backend/presets/FluxKlein9b", "img2img", None), "References")
    assert _all_field_names(references) == {"input_image", "input_image_2"}


def test_quantity_is_a_stepper_with_the_original_range(loader, serializer):
    """Rule: quantity's type moves to stepper, but its min/max/step must not
    change (the ComfyUI pipe loops on it -- a max of 4 must stay 4, not grow
    to the native-preset convention of 10)."""
    for suffix, mode, variant, _ in CASES:
        preset = _preset(loader, suffix)
        if "quantity" not in _all_field_names({"children": _tabs(loader, serializer, suffix, mode, variant)}):
            continue
        generation = _by_title(_tabs(loader, serializer, suffix, mode, variant), "Generation")

        def _find(node, name):
            for child in node.get("children") or []:
                if child.get("name") == name:
                    return child
                found = _find(child, name)
                if found:
                    return found
            return None

        quantity = _find(generation, "quantity")
        if quantity is None:
            continue
        assert quantity["type"] == "stepper", f"{suffix}/{mode}/{variant} quantity is {quantity['type']!r}"
        assert quantity["minimum"] == 1
        assert quantity["maximum"] == 4
        assert quantity["step"] == 1
