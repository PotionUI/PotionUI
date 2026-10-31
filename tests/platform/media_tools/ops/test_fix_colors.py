from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import InvalidToolParams, ToolFailed, run_chain, validate_params
from src.platform.media_tools.ops import fix_colors
from tests.platform.media_tools.ops.raw_runner import raw_context, video_media


def cast_clip(count=8):
    rng = np.random.default_rng(11)
    base = rng.integers(60, 190, size=(count, 36, 64, 1)).astype(np.float64)
    return np.clip(base * np.array([1.3, 1.0, 0.7]), 0, 255).astype(np.uint8)


def run_fix(tmp_path, params, frames=None):
    ctx = raw_context(tmp_path, cast_clip() if frames is None else frames)
    spec = fix_colors.spec()
    result = run_chain([(spec, params)], [video_media(tmp_path)], ctx)
    return result, ctx.runner


def video_filter(runner):
    command = runner.encodes()[0]
    return command[command.index("-vf") + 1]


def test_spec_is_a_video_effect_with_the_documented_params():
    spec = fix_colors.spec()

    assert spec.group == "effects" and spec.accepts == ("video",)
    assert [p["name"] for p in spec.params] == ["white_balance", "contrast", "strength"]


def test_a_colour_cast_produces_a_channel_mixer_pulling_red_down_and_blue_up(tmp_path):
    result, runner = run_fix(tmp_path, {"white_balance": True, "contrast": False, "strength": 100})

    assert len(result) == 1 and result[0].kind == "video"
    mixer = next(f for f in video_filter(runner).split(",") if f.startswith("colorchannelmixer"))
    gains = dict(part.split("=") for part in mixer.split("=", 1)[1].split(":"))
    assert float(gains["rr"]) < 1.0 < float(gains["bb"])
    assert "colorlevels" not in video_filter(runner)


def test_contrast_alone_emits_levels_and_no_mixer(tmp_path):
    flat = np.random.default_rng(2).integers(90, 170, size=(8, 36, 64, 3)).astype(np.uint8)

    _, runner = run_fix(tmp_path, {"white_balance": False, "contrast": True, "strength": 100}, flat)

    chain = video_filter(runner)
    assert "colorlevels" in chain and "colorchannelmixer" not in chain


def test_strength_zero_leaves_the_grade_out(tmp_path):
    _, runner = run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 0})

    command = " ".join(runner.encodes()[0])
    assert "colorchannelmixer" not in command and "colorlevels" not in command


def test_frames_are_sampled_at_low_rate_without_audio_and_cleaned_up(tmp_path):
    _, runner = run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 100})

    decode = runner.decodes()[0]
    assert "-an" in decode and "fps=2.000000,scale=96:54:flags=area" in decode
    assert not list((tmp_path / "scratch").glob("frames-*.rgb"))


def test_turning_both_corrections_off_is_rejected():
    with pytest.raises(InvalidToolParams):
        fix_colors.spec().impl.check_params({"white_balance": False, "contrast": False})


def test_defaults_validate():
    cleaned = validate_params(fix_colors.spec(), {})

    assert cleaned["white_balance"] is True and cleaned["strength"] == 100


def test_a_failure_after_decoding_leaves_no_frame_dump(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise ToolFailed("analysis broke")

    monkeypatch.setattr(fix_colors, "estimate_fix", broken)

    with pytest.raises(ToolFailed, match="analysis broke"):
        run_fix(tmp_path, {"white_balance": True, "contrast": True, "strength": 100})

    assert list((tmp_path / "scratch").glob("frames-*.rgb")) == []


def test_a_failed_encode_leaves_no_partial_output(tmp_path):
    ctx = raw_context(tmp_path, cast_clip(), fail_encode=1)

    with pytest.raises(Exception):
        run_chain([(fix_colors.spec(), {"white_balance": True, "contrast": True, "strength": 100})], [video_media(tmp_path)], ctx)

    assert [p for p in (tmp_path / "scratch").rglob("*") if p.is_file()] == []
