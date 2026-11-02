from __future__ import annotations

import pytest

from src.platform.media_tools import MediaTool, MediaToolRegistry
from src.platform.media_tools.ops import CORE_MODULES, core_specs, register_core_tools
from tests.platform.media_tools.fake_tools import make_spec

CORE_IDS = [
    "trim", "crop", "resize", "rotate", "flip", "speed",
    "fit_canvas", "color_key", "crop_subject", "frame", "split", "concat", "fix_colors", "film_grain", "cut_finder",
    "mute", "volume", "replace_audio", "paint_layer", "slice",
]
EDITOR_ONLY = ("replace_audio", "paint_layer", "slice")
AUDIO_GROUP = ("mute", "volume", "replace_audio")


def plugin_spec(plugin="boomerang", tool="loop", **overrides):
    values = dict(source=plugin, accepts=("video",))
    values.update(overrides)
    return make_spec(f"{plugin}.{tool}", MediaTool(), **values)


def test_core_registers_exactly_the_contract_tools_in_menu_order():
    registry = MediaToolRegistry()

    register_core_tools(registry)

    assert [spec.id for spec in registry.list()] == CORE_IDS
    assert len(CORE_MODULES) == 20
    assert all(spec.source == "core" and "." not in spec.id for spec in registry.list())


def test_core_specs_follow_the_contract_table():
    by_id = {spec.id: spec for spec in core_specs()}

    assert by_id["trim"].accepts == ("video", "audio") and by_id["trim"].editor == "trim"
    assert by_id["crop"].accepts == ("image", "video") and by_id["crop"].editor == "crop"
    assert by_id["resize"].editor == "resize"
    assert by_id["rotate"].preview and by_id["flip"].preview
    assert by_id["speed"].accepts == ("video", "audio") and not by_id["speed"].preview
    assert by_id["fit_canvas"].accepts == ("image",) and by_id["fit_canvas"].preview
    assert by_id["color_key"].preview
    assert by_id["crop_subject"].requires == {"has_alpha": True}
    assert by_id["frame"].produces == "image" and by_id["frame"].editor == "frame"
    assert by_id["split"].cardinality == "one_to_many" and by_id["split"].editor == "split"
    assert by_id["split"].accepts == ("audio", "video") and by_id["split"].produces == "same"
    assert by_id["mute"].accepts == ("video",) and by_id["mute"].requires == {"has_audio": True}
    assert by_id["volume"].accepts == ("video", "audio") and by_id["volume"].requires == {"has_audio": True}
    assert by_id["replace_audio"].accepts == ("video",) and by_id["paint_layer"].accepts == ("image",)
    assert all(by_id[tool].group == "audio" for tool in AUDIO_GROUP)
    assert all(by_id[tool].scopes == ("editor",) for tool in EDITOR_ONLY)
    assert by_id["fix_colors"].accepts == ("video",) and by_id["fix_colors"].group == "effects"
    assert by_id["film_grain"].accepts == ("video",) and by_id["film_grain"].group == "effects"
    assert by_id["film_grain"].cardinality == "one_to_one" and not by_id["film_grain"].preview
    assert by_id["slice"].accepts == ("image",) and by_id["slice"].cardinality == "one_to_many"
    assert by_id["cut_finder"].accepts == ("video",) and by_id["cut_finder"].cardinality == "one_to_many"
    assert all(
        spec.group == "edit"
        for spec in by_id.values()
        if spec.id not in ("fix_colors", "film_grain", *AUDIO_GROUP)
    )
    assert by_id["concat"].cardinality == "many_to_one" and by_id["concat"].selection == {"min": 2, "max": 8}
    assert all(
        spec.scopes == ("library", "history", "field") for spec in by_id.values() if spec.id not in EDITOR_ONLY
    )
    assert all(spec.impl is not None for spec in by_id.values())


def test_core_param_names_are_the_contract_names():
    expected = {
        "trim": ["start", "end"],
        "crop": ["x", "y", "width", "height"],
        "resize": ["width", "height", "quality"],
        "rotate": ["degrees"],
        "flip": ["axis"],
        "speed": ["speed", "keep_pitch"],
        "fit_canvas": ["width", "height", "scale_percent", "anchor", "fill"],
        "color_key": ["key_mode", "key_color", "tolerance", "softness", "despill", "feather"],
        "crop_subject": ["margin", "alpha_threshold"],
        "frame": ["time"],
        "split": ["mode", "part_seconds", "at"],
        "concat": ["overlap", "overlap_frames", "fit", "keep_audio"],
        "fix_colors": ["white_balance", "contrast", "strength"],
        "film_grain": ["amount", "size", "color", "seed"],
        "cut_finder": ["sensitivity", "min_shot_seconds"],
        "mute": [],
        "volume": ["unit", "percent", "db", "fade_in", "fade_out"],
        "replace_audio": ["audio", "start", "loop"],
        "paint_layer": ["layer"],
        "slice": ["rects", "trim", "tolerance", "padding", "aspect", "fill"],
    }

    assert {spec.id: [p["name"] for p in spec.params] for spec in core_specs()} == expected


def test_list_filters_by_kind_and_scope():
    registry = MediaToolRegistry()
    register_core_tools(registry)
    registry.register(plugin_spec(scopes=("history",)))

    assert [s.id for s in registry.list(kind="audio")] == ["trim", "speed", "split", "volume"]
    assert "replace_audio" not in [s.id for s in registry.list(kind="video", scope="library")]
    assert "replace_audio" in [s.id for s in registry.list(kind="video", scope="editor")]
    assert "boomerang.loop" in [s.id for s in registry.list(kind="video", scope="history")]
    assert "boomerang.loop" not in [s.id for s in registry.list(kind="video", scope="library")]
    assert registry.list(kind="audio", scope="history")[0].id == "trim"


def test_core_tools_are_listed_before_plugin_tools():
    registry = MediaToolRegistry()
    registry.register(plugin_spec())
    register_core_tools(registry)

    listed = [spec.source for spec in registry.list()]

    assert listed == sorted(listed, key=lambda source: source != "core")
    assert listed[-1] == "boomerang"


def test_unregister_source_removes_only_that_sources_tools():
    registry = MediaToolRegistry()
    register_core_tools(registry)
    registry.register(plugin_spec("a", "one"))
    registry.register(plugin_spec("a", "two"))
    registry.register(plugin_spec("b", "one"))

    removed = registry.unregister_source("a")

    assert removed == 2
    assert registry.get("a.one") is None and registry.get("b.one") is not None
    assert len(registry.list()) == len(CORE_MODULES) + 1
    assert registry.unregister_source("a") == 0
    assert registry.sources() == {"core", "b"}


def test_same_source_may_re_register_an_id():
    registry = MediaToolRegistry()
    registry.register(plugin_spec())

    registry.register(plugin_spec(label="Again"))

    assert registry.get("boomerang.loop").label == "Again"
    assert len(registry.list()) == 1


def test_get_unknown_id_is_none():
    assert MediaToolRegistry().get("nope") is None


@pytest.mark.parametrize(
    "spec",
    [
        make_spec("a.b", MediaTool(), source="core", accepts=("image",)),
        make_spec("loop", MediaTool(), source="boomerang", accepts=("video",)),
        make_spec("other.loop", MediaTool(), source="boomerang", accepts=("video",)),
        make_spec("x", MediaTool(), accepts=()),
        make_spec("x", MediaTool(), accepts=("mesh",)),
        make_spec("x", MediaTool(), accepts=("image",), produces="mesh"),
        make_spec("x", MediaTool(), accepts=("image",), cardinality="many"),
        make_spec("x", None, accepts=("image",)),
        make_spec("x", MediaTool(), accepts=("image",), params=[{"name": "n", "type": "vector"}]),
        make_spec("x", MediaTool(), accepts=("image",), params=[{"name": "n", "type": "string"}, {"name": "n", "type": "string"}]),
        make_spec("x", MediaTool(), accepts=("image",), params=[{"type": "string"}]),
        make_spec("", MediaTool(), accepts=("image",)),
    ],
)
def test_register_refuses_malformed_specs(spec):
    registry = MediaToolRegistry()

    with pytest.raises(ValueError):
        registry.register(spec)

    assert registry.list() == []


def test_component_only_spec_is_valid_without_an_implementation():
    registry = MediaToolRegistry()

    registry.register(make_spec("p.view", None, source="p", accepts=("image",), component="plugin:p:View.js"))

    assert registry.get("p.view").component == "plugin:p:View.js"


def test_to_dict_leaks_no_implementation_and_copies_params():
    spec = make_spec(
        "x",
        MediaTool(),
        accepts=("image",),
        params=[{"name": "n", "type": "integer", "default": 1}],
        selection={"min": 1},
        description="d",
    )

    data = spec.to_dict()
    data["params"][0]["default"] = 99
    data["accepts"].append("video")

    assert "impl" not in data and "name_suffix" not in data
    assert spec.params[0]["default"] == 1
    assert spec.accepts == ("image",)
    assert set(data) == {
        "id", "label", "icon", "accepts", "produces", "cardinality", "params", "requires",
        "selection", "scopes", "editor", "preview", "component", "source", "description", "group",
    }


@pytest.mark.parametrize(
    "cardinality,selection,expected",
    [
        ("one_to_one", None, (1, None)),
        ("one_to_many", None, (1, None)),
        ("many_to_one", None, (2, None)),
        ("many_to_one", {"min": 2, "max": 8}, (2, 8)),
        ("one_to_one", {"max": 3}, (1, 3)),
    ],
)
def test_selection_bounds(cardinality, selection, expected):
    spec = make_spec("x", MediaTool(), cardinality=cardinality, selection=selection)

    assert spec.selection_bounds() == expected


def test_output_kind_follows_produces():
    assert make_spec("x", MediaTool(), produces="same").output_kind("video") == "video"
    assert make_spec("x", MediaTool(), produces="image").output_kind("video") == "image"
