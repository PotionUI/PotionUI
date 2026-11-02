from __future__ import annotations

import os

import pytest

from src.platform.media_tools import MediaToolRegistry
from src.platform.media_tools.effects.loader import EffectLoader, find_effect, load_effect, register_effects
from src.platform.media_tools.effects.spec import EffectError, check_shader
from tests.platform.media_tools.effects.builder import BASE, SHADER, effect_data, write_effect

NO_CASE_PARAMS = {**BASE["parity"], "cases": [{"params": {}, "frames": [0]}]}
FFMPEG_ONLY = [{"ffmpeg": {"vf": [{"colorlevels": {"rimax": "{k}"}}]}}]


def test_a_valid_folder_loads_with_its_shader_render_and_uniforms(tmp_path):
    effect = load_effect(write_effect(tmp_path), "core")

    assert (effect.id, effect.tool_id, effect.group, effect.time) == ("dim", "dim", "colour", "none")
    assert effect.shader == SHADER
    assert effect.shader_etag().startswith('"') and len(effect.shader_etag()) == 18
    assert callable(effect.render[0].function)
    assert effect.uniforms() == [
        {"name": "u_strength", "type": "float", "from": "param", "key": "strength"},
        {"name": "u_k", "type": "float", "from": "derive", "key": "k"},
    ]


def test_a_plugin_effect_is_registered_under_the_plugin_id(tmp_path):
    effect = load_effect(write_effect(tmp_path), "retro-tape")

    assert effect.tool_id == "retro-tape.dim" and effect.source == "retro-tape"


def test_uniform_types_follow_the_param_types(tmp_path):
    params = [
        {"name": "level", "type": "integer", "label": "L", "default": 1, "min": 0, "max": 9},
        {"name": "on", "type": "checkbox", "label": "On", "default": True},
        {"name": "tint", "type": "color", "label": "Tint", "default": "#000000"},
        {"name": "mode", "type": "select", "label": "Mode", "default": "linear", "options": ["linear", "cubic"]},
        {"name": "at", "type": "anchor", "label": "At", "default": "top_left"},
    ]
    data = effect_data(params=params, derive={"k": "level / 10"}, parity=NO_CASE_PARAMS)

    effect = load_effect(write_effect(tmp_path, data=data), "core")

    assert [(row["name"], row["type"]) for row in effect.uniforms()] == [
        ("u_level", "int"), ("u_on", "bool"), ("u_tint", "vec3"), ("u_mode", "int"), ("u_at", "vec2"), ("u_k", "float"),
    ]


def param(**changes):
    return [{**BASE["params"][0], **changes}]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"id": None}, "'id' is required"),
        ({"id": "Dim"}, "id must be"),
        ({"label": None}, "'label' is required"),
        ({"group": "Colour Grading"}, "group must be"),
        ({"accepts": ["image"]}, "accepts"),
        ({"accepts": ["video", "audio"]}, "accepts"),
        ({"time": "later"}, "time must be"),
        ({"bind": {"x": 1}}, "unknown keys"),
        ({"requires": {"has_alpha": True}}, "requires"),
        ({"params": param(type="string")}, "unsupported type"),
        ({"params": param(type="media")}, "unsupported type"),
        ({"params": [{"name": "strength", "type": "float", "label": "S"}]}, "needs a default"),
        ({"params": param(default=500)}, "default"),
        ({"params": param(name="Strength")}, "param name"),
        ({"derive": {"strength": "1"}}, "declared twice"),
        ({"derive": {"media": "1", "k": "1"}}, "reserved"),
        ({"derive": {"k": "1", "min": "2"}}, "reserved"),
        ({"derive": {"k": "later + 1", "later": "1"}}, "'later' is not"),
        ({"derive": {"k": "import os"}}, "not a valid expression"),
        ({"analysis": {"python": "a.py", "values": ["x"]}}, "file.py:function"),
        ({"analysis": {"python": "render.py:apply", "keys": ["nope"], "values": ["x"]}}, "not a param"),
        ({"analysis": {"python": "render.py:apply", "values": []}}, "at least one value"),
        ({"render": []}, "at least one candidate"),
        ({"render": [{"glsl": "x"}]}, "unknown render candidate"),
        ({"render": [{"numpy": "render.py:apply"}, {"numpy": "render.py:apply"}]}, "listed twice"),
        ({"render": [{"numpy": "../other/render.py:apply"}]}, "file.py:function"),
        ({"render": [{"ffmpeg": {"vf": [{"movie": {}}]}}]}, "never allowed"),
        ({"render": [{"ffmpeg": {"vf": [{"gblur": {"sigma": "{nope}"}}]}}]}, "unknown placeholder"),
        ({"render": [{"ffmpeg": {"vf": [{"gblur": {"sigma": "{media.depth}"}}]}}]}, "unknown placeholder"),
        ({"render": [{"ffmpeg": {"vf": [{"gblur": {"sigma": "{strength.x}"}}]}}]}, "unknown placeholder"),
        ({"render": [{"ffmpeg": {"vf": [{"hflip": {}, "when": "nope > 1"}]}}]}, "'nope' is not"),
        ({"preview": {"shader": "../x.frag"}}, "preview.shader"),
        ({"preview": {"gain": "volume * 2"}}, "'volume' is not"),
        ({"preview": {"textures": [{"uniform": "u_cube", "from": "strength", "kind": "lut3d"}]}}, "lut3d param"),
        ({"preview": {"shader": "preview.frag", "code": "x"}}, "unknown preview keys"),
        ({"parity": None}, "parity is required"),
        ({"parity": {**BASE["parity"], "cases": [{"params": {"nope": 1}, "frames": [0]}]}}, "unknown params"),
        ({"parity": {**BASE["parity"], "cases": [{"params": {}, "frames": [-1]}]}}, "frame numbers"),
        ({"parity": {**BASE["parity"], "tolerance": {"max_abs": 1}}}, "max_abs and mean_abs"),
    ],
)
def test_schema_errors_are_reported_with_the_effect_name(tmp_path, changes, message):
    with pytest.raises(EffectError, match=message):
        load_effect(write_effect(tmp_path, data=effect_data(**changes)), "core")


def test_a_gain_may_read_time_and_an_anchor_may_be_read_by_axis(tmp_path):
    data = effect_data(
        params=[*BASE["params"], {"name": "at", "type": "anchor", "label": "At", "default": "center"}],
        preview={"shader": "preview.frag", "gain": "clamp(time / media.duration, 0, 1)"},
        render=[{"ffmpeg": {"vf": [{"perspective": {"x0": "{at.x} * W"}}]}}],
    )

    effect = load_effect(write_effect(tmp_path, data=data), "core")

    assert effect.gain.source == "clamp(time / media.duration, 0, 1)"


@pytest.mark.parametrize(
    "shader, message",
    [
        ("#version 300 es\n" + SHADER, "#version"),
        ("#extension GL_EXT_foo : enable\n" + SHADER, "#extension"),
        ("#include \"x\"\n" + SHADER, "#include"),
        ("  #line 4\n" + SHADER, "#line"),
        ("uniform float u_secret;\n" + SHADER, "declare uniform"),
        ("in vec2 v_other;\n" + SHADER, "declare uniform"),
        ("out vec4 o_other;\n" + SHADER, "declare uniform"),
        ("layout(location = 1) out vec4 extra;\n" + SHADER, "declare uniform"),
        ("float a; flat in int b;\n" + SHADER, "declare uniform"),
        ("vec4 other(vec4 color) { return color; }", "must define"),
        ("// " + "x" * (64 * 1024) + "\n" + SHADER, "64 KB"),
    ],
)
def test_shaders_the_host_cannot_wrap_are_refused(shader, message):
    with pytest.raises(EffectError, match=message):
        check_shader(shader, "dim")


def test_shader_parameters_and_comments_may_use_in_and_out():
    shader = (
        "// the host owns uniform declarations\n"
        "/* out of range in some cases */\n"
        "void split(in vec3 rgb, out float luma) { luma = dot(rgb, vec3(0.3)); }\n"
        "vec4 effect(vec4 color, vec2 uv) { float l; split(color.rgb, l); return vec4(vec3(l), color.a); }\n"
    )

    check_shader(shader, "dim")


def test_a_shader_symlinked_from_outside_the_folder_is_refused(tmp_path):
    outside = tmp_path / "secret.frag"
    outside.write_text(SHADER, encoding="utf-8")
    folder = write_effect(tmp_path / "root", shader=None)
    try:
        os.symlink(outside, folder / "preview.frag")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")

    with pytest.raises(EffectError, match="inside the effect folder"):
        load_effect(folder, "core")


@pytest.mark.parametrize(
    "files, render, message",
    [
        ({}, [{"numpy": "render.py:missing"}], "is not a function"),
        ({}, [{"numpy": "absent.py:apply"}], "inside the effect folder"),
        ({"broken.py": "raise RuntimeError('no')\n"}, [{"numpy": "broken.py:apply"}], "failed to load"),
    ],
)
def test_render_callables_must_load_from_the_folder(tmp_path, files, render, message):
    folder = write_effect(tmp_path, data=effect_data(render=render), files=files)

    with pytest.raises(EffectError, match=message):
        load_effect(folder, "core")


def test_a_select_value_that_is_not_an_enum_word_fails_the_load_time_template_check(tmp_path):
    params = [{"name": "mode", "type": "select", "label": "Mode", "default": "rm -rf", "options": ["rm -rf"]}]
    data = effect_data(
        params=params, derive={"k": "1"}, parity=NO_CASE_PARAMS, render=[{"ffmpeg": {"vf": [{"fade": {"t": "{mode}"}}]}}],
    )

    with pytest.raises(EffectError, match="cannot enter a filtergraph"):
        load_effect(write_effect(tmp_path, data=data), "core")


def test_an_analysis_function_is_loaded_with_its_values(tmp_path):
    files = {"analyze.py": "def measure(ctx, params):\n    return {'level': 0.5}\n"}
    data = effect_data(
        analysis={"python": "analyze.py:measure", "keys": ["strength"], "values": ["level"]},
        derive={"k": "level * strength / 100"},
    )

    effect = load_effect(write_effect(tmp_path, data=data, files=files), "core")

    assert effect.analysis.function(None, {}) == {"level": 0.5}
    assert [row["from"] for row in effect.uniforms()] == ["param", "analysis", "derive"]


def test_a_root_reports_broken_effects_and_loads_the_rest(tmp_path):
    write_effect(tmp_path, "good")
    write_effect(tmp_path, "bad", data=effect_data(id="bad", time="sometimes"))
    write_effect(tmp_path, "twin", data=effect_data(id="good"))
    (tmp_path / "notes").mkdir()

    loaded = EffectLoader().load_root(tmp_path, "acme")

    assert [effect.tool_id for effect in loaded.effects] == ["acme.good"]
    assert [name for name, _ in loaded.errors] == ["bad", "twin"]
    assert "time must be" in loaded.errors[0][1] and "declared twice" in loaded.errors[1][1]
    assert EffectLoader().load_root(tmp_path / "missing", "acme").effects == []


def test_registering_adds_effect_tools_and_disabling_the_plugin_removes_them(tmp_path):
    write_effect(tmp_path, "dim")
    write_effect(tmp_path, "flat", data=effect_data(id="flat", render=FFMPEG_ONLY))
    registry = MediaToolRegistry()

    registered = register_effects(registry, EffectLoader().load_root(tmp_path, "acme").effects)

    assert registered == ["acme.dim", "acme.flat"]
    dim, flat = registry.get("acme.dim"), registry.get("acme.flat")
    assert dim.group == "effects" and dim.accepts == ("video", "image") and dim.name_suffix == "-dim"
    assert flat.accepts == ("video",)
    assert find_effect(registry, "acme.dim").label == "Dim"
    assert registry.unregister_source("acme") == 2 and find_effect(registry, "acme.dim") is None
