from __future__ import annotations

import math
import subprocess

import pytest

from src.platform.media_tools.effects.derive import evaluate
from src.platform.media_tools.effects.template import (
    MAX_STEP_CHARS,
    AssetPath,
    TemplateError,
    compile_template,
    format_value,
    parse_template,
)
from src.platform.util.video_transcode import find_ffmpeg

VALUES = {
    "r": 1.1,
    "g": 0.95,
    "lo": 0.04,
    "on": True,
    "count": 73856093,
    "tint": "#ff8800",
    "easing": "linear",
    "free_text": "x:y",
    "media.width": 640,
    "media.duration": 4.0,
    "cube": AssetPath("lut-0123456789ab.cube"),
}


def compiled(data, values=VALUES, condition=lambda expression: True):
    return compile_template(parse_template(data), values, condition)


def test_options_are_compiled_single_quoted_with_numbers_formatted():
    result = compiled({"vf": [{"colorchannelmixer": {"rr": "{r}", "gg": "{g}", "bb": 1}}]})

    assert result.vf == ["colorchannelmixer=rr='1.1':gg='0.95':bb='1'"]


def test_numbers_flags_colours_and_words_have_one_form_each():
    assert format_value(1 / 3) == "0.333333"
    assert format_value(73856093) == "73856093"
    assert format_value(2.0) == "2"
    assert format_value(True) == "1" and format_value(False) == "0"
    assert format_value("#ff8800") == "0xff8800"
    assert format_value("tetrahedral") == "tetrahedral"


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, "x:y", "#FF8800", "hello", None, [1], {"a": 1}])
def test_anything_else_cannot_enter_a_filtergraph(value):
    with pytest.raises(TemplateError):
        format_value(value)


NEVER = [
    "movie", "amovie", "sendcmd", "asendcmd", "zmq", "azmq", "subtitles", "ass", "drawtext", "ladspa", "lv2", "frei0r",
    "arnndn", "haldclut", "signature", "dnn_processing", "dnn_detect", "dnn_classify", "vidstabdetect", "vidstabtransform",
]


@pytest.mark.parametrize("name", NEVER)
def test_every_denied_filter_is_refused(name):
    with pytest.raises(TemplateError, match="never allowed"):
        parse_template({"vf": [{name: {}}]})
    with pytest.raises(TemplateError, match="never allowed"):
        parse_template({"af": [{name: {}}]})


@pytest.mark.parametrize("name", ["scale", "overlay", "split", "concat", "amix", "geq", "drawbox", "zoompan", "select"])
def test_filters_outside_the_allowlist_are_refused(name):
    with pytest.raises(TemplateError, match="not allowed"):
        parse_template({"vf": [{name: {}}]})


@pytest.mark.parametrize(
    "entry",
    [
        {"curves": {"psfile": "a.acv"}},
        {"colorlevels": {"file": "x"}},
        {"lut3d": {"filename": "x"}},
        {"vignette": {"x0file": "x"}},
    ],
)
def test_options_that_read_files_are_refused(entry):
    with pytest.raises(TemplateError, match="reads a file"):
        parse_template({"vf": [entry]})


def test_unknown_option_keys_are_refused():
    with pytest.raises(TemplateError, match="not allowed"):
        parse_template({"vf": [{"vignette": {"enable": "1"}}]})


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../lut.cube", "a/../../lut.cube", "C:\\lut.cube", "lut'.cube", "lut:x.cube", "lut;.cube", " lut.cube"],
)
def test_lut3d_takes_only_a_safe_host_staged_path(path):
    data = {"vf": [{"lut3d": {"file": "{cube}"}}]}
    with pytest.raises(TemplateError):
        compiled(data, {**VALUES, "cube": AssetPath(path)})


def test_lut3d_refuses_literal_paths_and_non_assets():
    with pytest.raises(TemplateError, match="staged by the host"):
        compiled({"vf": [{"lut3d": {"file": "lut.cube"}}]})
    with pytest.raises(TemplateError, match="staged by the host"):
        compiled({"vf": [{"lut3d": {"file": "{r}"}}]})
    assert compiled({"vf": [{"lut3d": {"file": "{cube}", "interp": "tetrahedral"}}]}).vf == [
        "lut3d=file='lut-0123456789ab.cube':interp='tetrahedral'"
    ]


def test_an_asset_cannot_enter_any_other_option():
    with pytest.raises(TemplateError, match="cannot take a file"):
        compiled({"vf": [{"gblur": {"sigma": "{cube}"}}]})


@pytest.mark.parametrize(
    "text",
    ["1'", "1:2", "1\\2", "[in]", "1;movie=x", "1[a]", "a=b", "1 \n 2", "$HOME", "1 # x", "'quoted'"],
)
def test_the_runtime_tokeniser_refuses_escape_characters(text):
    with pytest.raises(TemplateError):
        compiled({"vf": [{"gblur": {"sigma": text}}]})


def test_runtime_variables_belong_to_their_filter():
    assert compiled({"vf": [{"perspective": {"x0": "in * W / 100"}}]}).vf == ["perspective=x0='in * W / 100'"]
    with pytest.raises(TemplateError, match="'W' is not allowed"):
        compiled({"vf": [{"gblur": {"sigma": "W / 100"}}]})
    with pytest.raises(TemplateError, match="'system' is not allowed"):
        compiled({"vf": [{"perspective": {"x0": "system(1)"}}]})


def test_a_string_value_must_be_an_enum_word_or_colour():
    assert compiled({"vf": [{"fade": {"t": "in", "color": "{tint}"}}]}).vf == ["fade=t='in':color='0xff8800'"]
    with pytest.raises(TemplateError):
        compiled({"vf": [{"fade": {"t": "{free_text}"}}]})


def test_expr_names_expand_in_parentheses_and_nest():
    data = {
        "expr": {"p": "clip(in / {media.duration}, 0, 1)", "z": "(1 + {p})"},
        "vf": [{"perspective": {"x0": "{z} * W", "eval": "frame"}}],
    }

    assert compiled(data).vf == ["perspective=x0='((1 + (clip(in / 4, 0, 1)))) * W':eval='frame'"]


def test_expr_names_that_refer_to_each_other_in_a_loop_are_refused():
    data = {"expr": {"a": "{b}", "b": "{a}"}, "vf": [{"gblur": {"sigma": "{a}"}}]}

    with pytest.raises(TemplateError, match="loop"):
        compiled(data)


def test_unknown_placeholders_and_stray_braces_are_refused():
    with pytest.raises(TemplateError, match="unknown placeholder"):
        compiled({"vf": [{"gblur": {"sigma": "{missing}"}}]})
    with pytest.raises(TemplateError, match="braces"):
        parse_template({"vf": [{"gblur": {"sigma": "{R}"}}]})
    with pytest.raises(TemplateError, match="braces"):
        parse_template({"vf": [{"gblur": {"sigma": "1}"}}]})


def test_when_conditions_drop_entries_and_audio_filters_stay_audio():
    data = {
        "vf": [{"fade": {"t": "in", "d": 1}, "when": "on"}, {"hflip": {}, "when": "not on"}],
        "af": [{"afade": {"t": "in", "d": 1}, "when": "on"}],
    }

    result = compiled(data, condition=lambda expression: bool(evaluate(expression, {"on": True})))

    assert result.vf == ["fade=t='in':d='1'"] and result.af == ["afade=t='in':d='1'"]
    with pytest.raises(TemplateError, match="audio"):
        parse_template({"af": [{"hflip": {}}]})
    with pytest.raises(TemplateError, match="video"):
        parse_template({"vf": [{"afade": {}}]})


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"vf": "hflip"},
        {"vf": [{"hflip": {}, "vflip": {}}]},
        {"vf": [{"hflip": {}}], "graph": "movie=x"},
        {"vf": []},
        {"vf": [{"gblur": {"sigma": [1]}}]},
        {"vf": [{"gblur": "sigma=1"}]},
        {"vf": [{"gblur": {"sigma": 1}, "when": "import os"}]},
        {"expr": {"A": "1"}, "vf": [{"hflip": {}}]},
        {"vf": [{"Hflip": {}}]},
    ],
)
def test_malformed_templates_are_refused(data):
    with pytest.raises(TemplateError):
        parse_template(data)


def test_a_compiled_step_is_capped_at_16_kb():
    data = {"vf": [{"gblur": {"sigma": " + ".join(["1"] * (MAX_STEP_CHARS // 4))}}]}

    with pytest.raises(TemplateError, match="16 KB"):
        compiled(data)


@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg is not available")
def test_a_compiled_graph_with_commas_and_expressions_runs_in_ffmpeg():
    data = {
        "expr": {"p": "clip((in - 1) / 24, 0, 1)"},
        "vf": [
            {"colorchannelmixer": {"rr": "{r}", "gg": "{g}"}},
            {"colorlevels": {"rimin": "{lo}", "rimax": 0.9}},
            {"perspective": {"x0": "{p} * W / 10", "y0": "0", "interpolation": "linear", "eval": "frame"}},
            {"vignette": {"angle": "0.4", "dither": 0, "eval": "init"}},
        ],
    }
    graph = ",".join(["format=gbrp", *compiled(data).vf])

    completed = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=gray:size=32x32:rate=24", "-vf", graph, "-frames:v", "3", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )

    assert completed.returncode == 0, completed.stderr


def test_expr_names_that_double_at_every_level_stop_at_16_kb():
    names = [f"m{index}" for index in range(16)]
    expr = {name: f"{{{following}}} + {{{following}}}" for name, following in zip(names, names[1:])}
    expr[names[-1]] = "1"
    data = {"expr": expr, "vf": [{"gblur": {"sigma": "{m0}"}}]}

    with pytest.raises(TemplateError, match="expanded expr"):
        compiled(data)
