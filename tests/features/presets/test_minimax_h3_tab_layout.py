from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.form_serializer import PresetFormSerializer
from src.platform.templating.processor import TemplateProcessor

H3 = "01KX47H3MINIMAX000000000VA"
FAST = "01KX47H3FASTH30000000000VA"
VDN = "01KX47H3VDNMINIMAX000000VA"

WITH_REFERENCES = ["Generation", "References", "LoRA", "Advanced"]

TABS = {
    (H3, "video"): ["Generation", "LoRA", "Enhance", "Advanced"],
    (H3, "refs"): WITH_REFERENCES,
    (H3, "upscale"): WITH_REFERENCES,
    (H3, "refine_faces"): WITH_REFERENCES,
    (H3, "extend"): WITH_REFERENCES,
    (H3, "remix"): WITH_REFERENCES,
    (H3, "talk"): WITH_REFERENCES,
    (H3, "motion_transfer"): WITH_REFERENCES,
    (H3, "loop"): WITH_REFERENCES,
    (FAST, "video"): ["Generation", "LoRA", "Enhance", "Advanced"],
    (FAST, "refine_faces"): WITH_REFERENCES,
    (FAST, "extend"): WITH_REFERENCES,
    (VDN, "video"): ["Generation", "LoRA", "Advanced"],
}

REFERENCE_MEDIA = {
    (H3, "refs"): {"references"},
    (H3, "upscale"): {"input_video"},
    (H3, "refine_faces"): {"input_video"},
    (H3, "extend"): {"source_video"},
    (H3, "remix"): {"source_video", "pictures"},
    (H3, "talk"): {"portrait", "voice"},
    (H3, "motion_transfer"): {"input_image", "input_video"},
    (H3, "loop"): {"start_picture"},
    (FAST, "refine_faces"): {"input_video"},
    (FAST, "extend"): {"source_video"},
}

ICONS = {
    "Generation": "generation",
    "References": "image",
    "LoRA": "lora",
    "Enhance": "sparkles",
    "Advanced": "settings",
}

MEDIA_TYPES = {"image", "video", "audio", "media"}
KEYFRAME_MODES = {(H3, "video"), (FAST, "video"), (VDN, "video")}
STAGE_TWO_KNOBS = {"refine_video_sigma_shift", "refine_manual_sigmas", "refine_manual_audio_sigmas", "audio_refine"}
REFINE_MODES = {(H3, "video"), (H3, "upscale"), (FAST, "video")}

FIXED_RECIPES = {
    (H3, "upscale"): 4,
    (H3, "refine_faces"): 6,
    (FAST, "refine_faces"): 4,
}

PROFILELESS = {(H3, "loop")}

CASES = list(TABS)
IDS = [f"{'H3' if p == H3 else 'Fast' if p == FAST else 'VDN'}-{m}" for p, m in CASES]


@pytest.fixture(scope="module")
def schemas():
    loader = PresetTemplateLoader(["content/presets/marketplace"])
    loader.load_presets()
    serializer = PresetFormSerializer(loader, TemplateProcessor(settings=Mock()))
    presets = {p.id: p for p in loader.presets if p.id in {H3, FAST, VDN}}
    assert set(presets) == {H3, FAST, VDN}
    return {
        (pid, mode): serializer.process_form_fields(presets[pid].modes[mode].forms[0], pid)
        for pid, mode in CASES
    }


def _tabs(schema):
    return schema["properties"]["tabs"]["children"]


def _tab(schema, title):
    return next(t for t in _tabs(schema) if t.get("title") == title)


def _walk(node):
    for child in node.get("children") or []:
        yield child
        yield from _walk(child)


def _fields(node):
    return {n["name"]: n for n in _walk(node) if n.get("name") and n.get("type") not in ("tab", "tabs")}


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_tab_order_and_nothing_above_the_tabs(schemas, case):
    schema = schemas[case]
    assert [t.get("title") for t in _tabs(schema)] == TABS[case]
    assert list(schema["properties"]) == ["tabs"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_tabs_are_icon_only_with_the_standard_icons(schemas, case):
    for tab in _tabs(schemas[case]):
        assert tab["configuration"]["icon"] == ICONS[tab["title"]]
        assert tab["configuration"]["icon_display"] == "icon_only"
        assert (tab.get("audience") == "advanced") == (tab["title"] == "Advanced")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_generation_tab_is_speed_profile_then_video_then_models(schemas, case):
    children = _tab(schemas[case], "Generation")["children"]
    if case in PROFILELESS:
        assert [c.get("title") for c in children] == ["Video", "Models"]
        assert all(c["type"] == "section" for c in children)
        return
    assert children[0]["name"] == "speed_profile"
    assert [c.get("title") for c in children[1:]] == ["Video", "Models"]
    assert all(c["type"] == "section" for c in children[1:])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_video_section_opens_with_seed_and_a_quantity_capped_at_four(schemas, case):
    video = next(c for c in _tab(schemas[case], "Generation")["children"] if c.get("title") == "Video")
    first = video["children"][0]
    seed_and_quantity = first["children"] if first["type"] == "row" else [first]
    assert seed_and_quantity[0]["name"] == "seed"
    quantity = _fields(video).get("quantity")
    if quantity is not None:
        assert quantity["maximum"] == 4


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_model_picker_outside_enhance_sits_in_models(schemas, case):
    schema = schemas[case]
    models = _tab(schema, "Generation")["children"][-1]
    in_models = {n for n, f in _fields(models).items() if f["type"] == "model"}
    elsewhere = {
        n for tab in _tabs(schema) if tab["title"] != "Enhance"
        for n, f in _fields(tab).items() if f["type"] == "model"
    }
    assert in_models and elsewhere == in_models


@pytest.mark.parametrize("case", list(REFERENCE_MEDIA), ids=[i for c, i in zip(CASES, IDS) if c in REFERENCE_MEDIA])
def test_inputs_live_on_the_references_tab_and_nowhere_else(schemas, case):
    schema = schemas[case]
    refs = {n for n, f in _fields(_tab(schema, "References")).items() if f["type"] in MEDIA_TYPES}
    assert refs == REFERENCE_MEDIA[case]
    for tab in _tabs(schema):
        if tab["title"] != "References":
            assert not {n for n, f in _fields(tab).items() if f["type"] in MEDIA_TYPES}


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_keyframe_budget_only_where_keyframes_exist(schemas, case):
    names = set(_fields(schemas[case]["properties"]["tabs"]))
    assert ("keyframe_pixel_budget" in names) == (case in KEYFRAME_MODES)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_refine_knobs_only_where_a_refine_runs(schemas, case):
    names = set(_fields(schemas[case]["properties"]["tabs"]))
    if case in REFINE_MODES:
        assert STAGE_TWO_KNOBS <= names
    else:
        assert not STAGE_TWO_KNOBS & names


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_steps_on_advanced_follow_every_offered_profile(schemas, case):
    if case in PROFILELESS:
        pytest.skip("no profile select")
    schema = schemas[case]
    steps = _fields(_tab(schema, "Advanced"))["steps"]
    offered = [o["value"] for o in _fields(_tab(schema, "Generation"))["speed_profile"]["options"]]
    for profile in offered:
        assert any(
            r["when"]["field"] == "speed_profile" and r["when"]["value"] == profile
            and r["then"]["set_disabled"] is not None
            for r in steps["reactions"]
        ), profile


@pytest.mark.parametrize("case", list(FIXED_RECIPES), ids=[i for c, i in zip(CASES, IDS) if c in FIXED_RECIPES])
def test_fixed_recipes_stay_the_unpinned_default(schemas, case):
    schema = schemas[case]
    assert _fields(_tab(schema, "Generation"))["speed_profile"]["default"] == "custom"
    steps = _fields(_tab(schema, "Advanced"))["steps"]
    assert steps["default"] == FIXED_RECIPES[case]
    custom = [r for r in steps["reactions"] if r["when"]["value"] == "custom"]
    assert custom and all(r["then"].get("set_value") is None for r in custom)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_no_shipped_mode_carries_the_background_removal_fields(schemas, case):
    names = set(_fields(schemas[case]["properties"]["tabs"]))
    assert not {"remove_background", "matting_model"} & names


LOOP = (H3, "loop")


def test_the_loop_offers_no_speed_profile_it_cannot_close_with(schemas):
    schema = schemas[LOOP]
    names = set(_fields(schema["properties"]["tabs"]))
    assert "speed_profile" not in names
    assert "flex_adapter" not in names
    steps = _fields(_tab(schema, "Advanced"))["steps"]
    assert steps["default"] == 24
    assert steps["minimum"] >= 20
    assert not steps.get("reactions")


def test_the_loop_keeps_the_step_cache_the_other_modes_offer(schemas):
    loop = _fields(_tab(schemas[LOOP], "Advanced"))
    refs = _fields(_tab(schemas[(H3, "refs")], "Advanced"))
    assert {"step_cache_threshold", "step_cache_warmup_steps", "step_cache_max_skips"} <= set(loop)
    assert loop["step_cache_threshold"]["default"] == refs["step_cache_threshold"]["default"]


def test_the_loop_follows_the_tabs_and_input_placement_of_the_input_media_modes(schemas):
    for sibling in [(H3, "remix"), (H3, "extend"), (H3, "refs")]:
        assert [t.get("title") for t in _tabs(schemas[LOOP])] == [t.get("title") for t in _tabs(schemas[sibling])]
    assert list(_fields(_tab(schemas[LOOP], "References"))) == ["start_picture"]
    video = _fields(next(c for c in _tab(schemas[LOOP], "Generation")["children"] if c.get("title") == "Video"))
    assert {"seed", "quantity", "resolution", "loop_seconds", "movement", "sound"} <= set(video)
