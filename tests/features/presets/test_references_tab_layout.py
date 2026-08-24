"""Layout contract for the References tab.

Modes whose uploaded media CONDITIONS a generation (img2img, img2vid, flf2v,
ia2v, ref2va, edit, inpaint) carry their media loaders on a dedicated
"References" tab, positioned immediately after the mode's first tab and before
any LoRA tab. Modes whose media is the sole OPERAND of a transform (the
ImageTools/AudioTools/VideoTools utilities, the SeedVR2 and LTX upscalers, the
LTX-2-3 utility preset, WAN interpolation, TRELLIS2 img2mesh) deliberately do
NOT get the tab -- their input is the subject of the operation, not a
reference, and moving it would leave the first tab holding one stray slider.

The assertions run through the SAME path that serves `GET /api/presets/{id}/form`:
PresetTemplateLoader -> PresetFormSerializer.process_form_fields. That matters
here because a tab body arrives as a `children:` STRING (a Jinja
`{{ paths.preset }}` path) and only becomes fields inside the serializer's
`_resolve_external_children`. A fixture built by hand, or a `yaml.safe_load` of
the tab file, would assert against a tree the frontend never receives and would
pass even if the new references.yml were unreachable.

Tab order is plain list order in form.yml -- there is no `order:`/`position:`
key -- so index comparisons are the contract.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.plugins.loader import PluginLoader
from src.platform.templating.processor import TemplateProcessor

MEDIA_TYPES = {"image", "video", "audio", "media"}

# Plugins that either ship preset roots (`presets:`) or contribute a mode onto
# someone else's preset (`preset_modes:`). Loading them is what puts the
# comfyui-backend presets and krea2-edit's contributed `edit` mode in scope.
PLUGIN_IDS = {"comfyui-backend", "krea2-edit", "trellis2", "stable-audio"}

# (preset path suffix, mode, media field names expected on the References tab)
CASES = [
    ("presets/marketplace/Flux1", "img2img", {"source_image"}),
    ("presets/marketplace/Flux2", "img2img", {"source_image"}),
    ("presets/marketplace/QwenImage", "img2img", {"source_image"}),
    ("presets/marketplace/QwenImage", "edit", {"source_image"}),
    ("presets/marketplace/Krea2", "enhance", {"source_image"}),
    ("presets/marketplace/Krea2", "edit", {"source_image", "source_image_b"}),
    ("presets/marketplace/SDXL", "inpaint", {"source_image"}),
    ("presets/marketplace/MiniMax-H3", "refs", {"references", "reference_videos", "reference_audios"}),
    ("comfyui-backend/presets/FluxKlein9b", "img2img", {"input_image", "input_image_2"}),
    ("comfyui-backend/presets/LTX-2-3/official", "flf2v", {"first_frame", "last_frame"}),
    ("comfyui-backend/presets/LTX-2-3/official", "ia2v", {"source_image", "source_audio"}),
    ("comfyui-backend/presets/LTX-2-3/official", "img2vid", {"source_image"}),
    ("comfyui-backend/presets/LTX-2", "img2vid", {"source_image"}),
    ("comfyui-backend/presets/QwenImage", "img2img", {"source_image", "ref_image_2", "ref_image_3"}),
    ("comfyui-backend/presets/WAN_2_2/custom", "img2vid", {"source_image"}),
    ("comfyui-backend/presets/WAN_2_2/official", "img2vid", {"source_image"}),
    ("comfyui-backend/presets/WAN_2_2/svi20pro", "img2vid", {"source_image"}),
    ("comfyui-backend/presets/WAN_2_2/flf", "flf", {"start_image", "end_image"}),
    ("comfyui-backend/presets/WAN_2_2/perfectloop", "clip", {"source_image"}),
    ("comfyui-backend/presets/WAN_2_2/perfectloop", "vace", {"input_video"}),
]

# Reference-related SETTINGS that moved onto the tab alongside the media they
# modify: each only has meaning relative to the uploaded reference.
SETTING_CASES = [
    # Flux1/Flux2 img2img denoise deliberately moved off the tab (maintainer
    # tab rework, 2026-08-21) -- not a convention violation for either preset.
    # QwenImage img2img denoise deliberately moved off the tab (maintainer
    # tab rework, 2026-08-21) -- not a convention violation for that preset.
    ("presets/marketplace/SDXL", "inpaint", "denoise"),
    ("presets/marketplace/SDXL", "inpaint", "mask_blur"),
    ("presets/marketplace/MiniMax-H3", "refs", "reference_pixel_budget"),
    ("presets/marketplace/Krea2", "edit", "ref_boost"),
    ("comfyui-backend/presets/LTX-2-3/official", "ia2v", "switch_to_t2v"),
    ("comfyui-backend/presets/WAN_2_2/flf", "flf", "enable_loop"),
    ("comfyui-backend/presets/WAN_2_2/flf", "flf", "remove_first_frame"),
    ("comfyui-backend/presets/WAN_2_2/flf", "flf", "remove_last_frame"),
]

# Media-bearing modes that must stay as they are: the media is the operand of a
# transform, not a reference conditioning a generation.
UNTOUCHED_CASES = [
    ("presets/marketplace/ImageTools", "remove_background"),
    ("presets/marketplace/ImageTools", "crop_subject"),
    ("presets/marketplace/AudioTools", "trim"),
    ("presets/marketplace/VideoTools", "interpolate"),
    ("presets/marketplace/SeedVR2", "upscale"),
    ("presets/marketplace/SeedVR2", "video_upscale"),
    ("presets/marketplace/LTX-2", "upscale"),
    ("presets/marketplace/LTX-2.5", "upscale"),
    ("comfyui-backend/presets/LTX-2-3/utility", "effects"),
    ("comfyui-backend/presets/WAN_2_2/custom", "interpolation"),
    ("trellis2/presets/TRELLIS2", "img2mesh"),
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
    # The template processor is what expands `@loop` field templates; without
    # it any preset carrying a loop (SDXL's ControlNet accordions) raises
    # rather than serializing.
    return PresetFormSerializer(loader, TemplateProcessor(settings_manager=Mock()))


def _preset(loader, suffix):
    matches = [p for p in loader.presets if str(p.path).replace("\\", "/").endswith(suffix)]
    if not matches:
        pytest.skip(f"preset {suffix} not present")
    # Suffix matching must identify exactly one preset -- `presets/marketplace/QwenImage`
    # and `comfyui-backend/presets/QwenImage` are different presets.
    assert len(matches) == 1, f"{suffix} matched {len(matches)} presets"
    return matches[0]


def _tabs(loader, serializer, suffix, mode):
    """The tab list exactly as the form endpoint serves it, in order."""
    preset = _preset(loader, suffix)
    if mode not in preset.modes:
        # A mode absent here (rather than the whole preset, which `_preset`
        # already skips on) means a `preset_modes:`-contributing plugin from
        # PLUGIN_IDS - e.g. krea2-edit's `edit` mode on the core Krea2 preset
        # - isn't installed, same as a missing preset.
        pytest.skip(f"{suffix} has no mode {mode} (contributing plugin not present)")
    schema = serializer.process_form_fields(preset.modes[mode].forms[0], preset.id)
    container = schema["properties"].get("tabs")
    assert container is not None, f"{suffix}/{mode} form is not a tabs container"
    return container["children"]


def _titles(tabs):
    return [t.get("title") for t in tabs]


def _field_names_by_type(node, types):
    """Every field name of one of `types` anywhere under `node`."""
    found = set()
    for child in node.get("children") or []:
        if child.get("type") in types and child.get("name"):
            found.add(child["name"])
        found |= _field_names_by_type(child, types)
    return found


def _all_field_names(node):
    found = set()
    for child in node.get("children") or []:
        if child.get("name"):
            found.add(child["name"])
        found |= _all_field_names(child)
    return found


@pytest.mark.parametrize("suffix,mode,media", CASES, ids=[f"{s}-{m}" for s, m, _ in CASES])
def test_references_tab_exists_and_holds_the_media_loaders(loader, serializer, suffix, mode, media):
    tabs = _tabs(loader, serializer, suffix, mode)
    titles = _titles(tabs)
    assert "References" in titles, f"{suffix}/{mode} tabs are {titles}"
    tab = tabs[titles.index("References")]
    assert _field_names_by_type(tab, MEDIA_TYPES) == media


@pytest.mark.parametrize("suffix,mode,media", CASES, ids=[f"{s}-{m}" for s, m, _ in CASES])
def test_references_tab_sits_directly_after_the_first_tab(loader, serializer, suffix, mode, media):
    titles = _titles(_tabs(loader, serializer, suffix, mode))
    assert titles.index("References") == 1, f"{suffix}/{mode} tabs are {titles}"


@pytest.mark.parametrize("suffix,mode,media", CASES, ids=[f"{s}-{m}" for s, m, _ in CASES])
def test_references_tab_precedes_every_lora_tab(loader, serializer, suffix, mode, media):
    titles = _titles(_tabs(loader, serializer, suffix, mode))
    ref = titles.index("References")
    loras = [i for i, t in enumerate(titles) if t and "lora" in t.lower()]
    assert all(ref < i for i in loras), f"{suffix}/{mode} tabs are {titles}"


@pytest.mark.parametrize("suffix,mode,media", CASES, ids=[f"{s}-{m}" for s, m, _ in CASES])
def test_media_loaders_left_the_first_tab(loader, serializer, suffix, mode, media):
    """The point of the change: nothing was copied, it was moved."""
    tabs = _tabs(loader, serializer, suffix, mode)
    assert _field_names_by_type(tabs[0], MEDIA_TYPES) == set()


@pytest.mark.parametrize(
    "suffix,mode,field", SETTING_CASES, ids=[f"{s}-{m}-{f}" for s, m, f in SETTING_CASES]
)
def test_reference_related_setting_moved_onto_the_tab(loader, serializer, suffix, mode, field):
    tabs = _tabs(loader, serializer, suffix, mode)
    titles = _titles(tabs)
    tab = tabs[titles.index("References")]
    assert field in _all_field_names(tab)


@pytest.mark.parametrize("suffix,mode", UNTOUCHED_CASES, ids=[f"{s}-{m}" for s, m in UNTOUCHED_CASES])
def test_operand_modes_get_no_references_tab(loader, serializer, suffix, mode):
    titles = _titles(_tabs(loader, serializer, suffix, mode))
    assert "References" not in titles, f"{suffix}/{mode} tabs are {titles}"


def test_pure_text_to_image_mode_is_untouched(loader, serializer):
    """A mode with no uploaded media must not gain an empty References tab."""
    titles = _titles(_tabs(loader, serializer, "presets/marketplace/SDXL", "txt2img"))
    assert "References" not in titles


def test_no_tab_body_file_is_orphaned():
    """Every `tabs/*.yml` on disk is composed by some form.yml.

    Splitting fields into a new tab body is two edits -- write the file, then
    register it in form.yml -- and stopping after the first leaves a file that
    is individually valid YAML and individually valid against FieldSpec, so
    `preset_lint.py` reports nothing while the fields inside it have silently
    vanished from the UI. A `required:` field can disappear this way. The
    parametrized cases above catch it for the modes this change touched; this
    guard covers every preset root, including ones added later.

    Tab bodies are legitimately shared ACROSS modes of one preset (MiniMax-H3's
    `refs` mode composes `modes/video/tabs/lora.yml`), so a body counts as
    reachable if ANY form root in the same preset names it.
    """
    import glob
    import os
    import re

    roots = ["content/presets"]
    for manifest in glob.glob("content/plugins/*/*/manifest.yml"):
        text = Path(manifest).read_text()
        if "presets:" in text or "preset_modes:" in text:
            roots.append(os.path.dirname(manifest))

    tab_files = [
        f for root in roots
        for f in glob.glob(f"{root}/**/tabs/*.yml", recursive=True)
        if "node_modules" not in f
    ]
    assert tab_files, "no tab bodies discovered - the globs above are wrong"

    orphans = []
    for tab_file in sorted(tab_files):
        preset_dir = tab_file.split("/modes/")[0]
        composed = set()
        for form in glob.glob(f"{preset_dir}/modes/*/form.yml") + glob.glob(
            f"{preset_dir}/modes/*/variants/*/form.yml"
        ):
            # `children:` is a Jinja path; only `{{ paths.preset }}` is
            # substituted, and textually (see docs/presets.md).
            for ref in re.findall(r'children:\s*"([^"]+)"', Path(form).read_text()):
                composed.add(os.path.normpath(ref.replace("{{ paths.preset }}", preset_dir)))
        if os.path.normpath(tab_file) not in composed:
            orphans.append(tab_file)

    assert orphans == [], f"tab bodies no form.yml composes: {orphans}"
