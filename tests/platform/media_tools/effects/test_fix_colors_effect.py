from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.platform.media_tools import ToolFailed, ToolMedia, run_chain, validate_params
from src.platform.media_tools.effects.tool import ANALYSIS_KEY
from src.platform.media_tools.fastpath import RGB_IN
from src.platform.util.video_transcode import find_ffmpeg
from tests.platform.media_tools.effects.support import core_effect, effect_spec, numpy_only
from tests.platform.media_tools.ops.raw_runner import raw_context, video_media

MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=2.0, fps=24.0)
MEASURED = {"gain_r": 0.8, "gain_g": 1.0, "gain_b": 1.25, "black": 0.05, "white": 0.9}


def cast_clip(count=8):
    rng = np.random.default_rng(11)
    base = rng.integers(60, 190, size=(count, 36, 64, 1)).astype(np.float64)
    return np.clip(base * np.array([1.3, 1.0, 0.7]), 0, 255).astype(np.uint8)


def run_fix(tmp_path, params, frames=None):
    ctx = raw_context(tmp_path, cast_clip() if frames is None else frames)
    result = run_chain([(effect_spec("fix_colors"), params)], [video_media(tmp_path)], ctx)
    return result, ctx.runner


def video_filter(runner):
    command = runner.encodes()[0]
    return command[command.index("-vf") + 1]


def test_spec_is_a_video_effect_with_the_documented_params_and_an_analysis():
    spec = effect_spec("fix_colors")
    effect = core_effect("fix_colors")

    assert spec.group == "effects" and spec.accepts == ("video",) and spec.name_suffix == "-colors"
    assert [p["name"] for p in spec.params] == ["white_balance", "contrast", "strength"]
    assert effect.group == "colour" and effect.analysis.values == ("gain_r", "gain_g", "gain_b", "black", "white")
    assert validate_params(spec, {}) == {"white_balance": True, "contrast": True, "strength": 100}


def test_a_colour_cast_produces_a_channel_mixer_pulling_red_down_and_blue_up(tmp_path):
    result, runner = run_fix(tmp_path, {"white_balance": True, "contrast": False, "strength": 100})

    assert len(result) == 1 and result[0].kind == "video"
    chain = video_filter(runner).split(",")
    mixer = next(f for f in chain if f.startswith("colorchannelmixer"))
    gains = {key: float(value.strip("'")) for key, value in (part.split("=") for part in mixer.split("=", 1)[1].split(":"))}
    assert gains["rr"] < 1.0 < gains["bb"]
    assert not any(f.startswith("colorlevels") for f in chain)
    assert chain.index(RGB_IN[1]) < chain.index(mixer)


def test_contrast_alone_emits_levels_and_no_mixer(tmp_path):
    flat = np.random.default_rng(2).integers(90, 170, size=(8, 36, 64, 3)).astype(np.uint8)

    _, runner = run_fix(tmp_path, {"white_balance": False, "contrast": True, "strength": 100}, flat)

    chain = video_filter(runner)
    assert "colorlevels" in chain and "colorchannelmixer" not in chain


def test_strength_zero_leaves_the_grade_and_the_colour_conversion_out(tmp_path):
    _, runner = run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 0})

    command = " ".join(runner.encodes()[0])
    assert "colorchannelmixer" not in command and "colorlevels" not in command and "gbrp" not in command


def test_frames_are_sampled_at_low_rate_without_audio_and_cleaned_up(tmp_path):
    _, runner = run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 100})
    decode = runner.decodes()[0]
    assert "-an" in decode and "fps=2.000000,scale=96:54:flags=area" in decode
    assert not list((tmp_path / "scratch").glob("frames-*.rgb"))


def test_an_analysis_failure_leaves_no_frame_dump(tmp_path, monkeypatch):
    function = core_effect("fix_colors").analysis.function

    def broken(*_args, **_kwargs):
        raise ToolFailed("analysis broke")

    monkeypatch.setitem(function.__globals__, "estimate_color_fix", broken)

    with pytest.raises(ToolFailed, match="analysis broke"):
        run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 100})

    assert list((tmp_path / "scratch").glob("frames-*.rgb")) == []


def test_a_failed_encode_leaves_no_partial_output(tmp_path):
    ctx = raw_context(tmp_path, cast_clip(), fail_encode=1)

    with pytest.raises(Exception):
        run_chain([(effect_spec("fix_colors"), {"white_balance": True, "contrast": True, "strength": 100})], [video_media(tmp_path)], ctx)

    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []


def test_the_derive_scales_the_measured_fix_by_strength():
    spec = effect_spec("fix_colors")
    params = {**validate_params(spec, {"strength": 50}), ANALYSIS_KEY: MEASURED}

    fragment = spec.impl.video_filter(MEDIA, params)

    assert fragment.vf == [
        "colorchannelmixer=rr='0.9':gg='1':bb='1.125'",
        "colorlevels=rimin='0.025':gimin='0.025':bimin='0.025':rimax='0.95':gimax='0.95':bimax='0.95'",
    ]


@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg is not available")
@pytest.mark.parametrize("strength", [100, 40])
def test_the_numpy_fallback_matches_the_ffmpeg_render(strength):
    frames = np.random.default_rng(strength).integers(0, 256, size=(2, 18, 24, 3), dtype=np.uint8)
    params = {**validate_params(effect_spec("fix_colors"), {"strength": strength}), ANALYSIS_KEY: MEASURED}
    media = MEDIA.with_changes(width=24, height=18)
    graph = effect_spec("fix_colors").impl.video_filter(media, params).vf
    numpy_stage = effect_spec("fix_colors", numpy_only()).impl.video_filter(media, params).stage

    completed = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", "24x18", "-r", "24", "-i", "pipe:0", "-vf", ",".join(["format=gbrp", *graph, "format=rgb24"]),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
        input=frames.tobytes(), capture_output=True, timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = np.frombuffer(completed.stdout, dtype=np.uint8).reshape(frames.shape)
    fallback = numpy_stage.apply(frames, (0, 1), 24.0, 2.0)
    difference = np.abs(rendered.astype(int) - fallback.astype(int))
    assert difference.max() <= 1 and difference.mean() <= 0.1
