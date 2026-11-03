from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.platform.media_tools import InvalidToolParams, ToolMedia, probe, run_chain, validate_params
from src.platform.media_tools.effects.derive import evaluate
from src.platform.util.video_transcode import find_ffmpeg
from tests.platform.media_tools.effects.support import core_effect, effect_spec
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg

MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=4.0, fps=25.0, has_audio=True)


def bound(params, media=MEDIA):
    spec = effect_spec("fade")
    return spec.impl.bind(media, validate_params(spec, params))


def fragment(params, media=MEDIA):
    spec = effect_spec("fade")
    return spec.impl.video_filter(media, validate_params(spec, params))


def test_spec_is_a_transition_in_seconds_with_short_descriptions():
    spec = effect_spec("fade")
    effect = core_effect("fade")

    assert spec.accepts == ("video",) and effect.group == "transitions" and effect.time == "frame"
    assert [candidate.kind for candidate in effect.render] == ["ffmpeg", "numpy"]
    assert [p["name"] for p in spec.params] == ["fade_in", "fade_out", "sound"]
    assert all(p.get("unit") == "s" for p in spec.params[:2])
    assert all(len(p["description"].split()) <= 5 for p in spec.params)
    assert validate_params(spec, {}) == {"fade_in": 1, "fade_out": 1, "sound": True}
    assert effect.gain is not None


@pytest.mark.parametrize("params", [{"fade_in": -0.1}, {"fade_out": 10.5}, {"color": "#ffffff"}])
def test_out_of_bounds_or_unknown_params_are_refused(params):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("fade"), params)


@pytest.mark.parametrize(
    "params, media, derived",
    [
        ({}, MEDIA, {"frames": 100, "in_frames": 25, "out_frames": 25, "out_frame": 74, "last": 3.96, "fin": 1.0, "fout": 1.0, "out_start": 2.96}),
        ({"fade_in": 0.5, "fade_out": 0}, MEDIA, {"in_frames": 12, "out_frames": 0, "out_frame": 99, "fin": 0.48, "fout": 0.0, "out_start": 3.96}),
        ({"fade_in": 10, "fade_out": 10}, MEDIA, {"in_frames": 99, "out_frames": 99, "out_frame": 0, "fin": 3.96, "fout": 3.96, "out_start": 0.0}),
        ({"fade_in": 0.1}, MEDIA.with_changes(fps=30.0, duration=2.0), {"frames": 60, "in_frames": 3, "out_frame": 29, "fin": 0.1, "out_start": 59 / 30 - 1}),
    ],
)
def test_the_derive_snaps_fades_to_whole_frames_and_ends_on_the_last_frame(params, media, derived):
    got = bound(params, media).derived
    assert {name: got[name] for name in derived} == pytest.approx(derived)


def test_the_template_compiles_to_a_fade_in_a_fade_out_and_matching_audio_fades():
    result = fragment({"fade_in": 1, "fade_out": 2})

    assert result.vf == [
        "fade=t='in':start_frame='0':nb_frames='25'",
        "fade=t='out':start_frame='49':nb_frames='50'",
    ]
    assert result.af == ["afade=t='in':st='0':d='1'", "afade=t='out':st='1.96':d='2'"]
    assert result.rgb is True and result.stage is None


def test_a_zero_length_side_and_silent_sound_drop_their_filters():
    silent = fragment({"fade_in": 0, "fade_out": 1, "sound": False})
    assert silent.vf == ["fade=t='out':start_frame='74':nb_frames='25'"]
    assert silent.af == []
    nothing = fragment({"fade_in": 0, "fade_out": 0})
    assert nothing.vf == [] and nothing.af == [] and nothing.rgb is False


@pytest.mark.parametrize("effect_id", ["fade", "ken_burns"])
@pytest.mark.parametrize("sound", [False, True])
def test_time_based_effects_count_frames_and_never_rewrite_timestamps(effect_id, sound):
    spec = effect_spec(effect_id)
    params = {"sound": sound} if effect_id == "fade" else {"zoom_to": 2}

    result = spec.impl.video_filter(MEDIA, validate_params(spec, params))

    assert not any("setpts" in item for item in (*result.vf, *result.af))


@pytest.mark.parametrize("time, expected", [(0.0, 0.0), (0.24, 0.24), (2.0, 1.0), (3.46, 0.5), (3.96, 0.0)])
def test_the_preview_gain_follows_the_picture(time, expected):
    effect = core_effect("fade")
    env = {**bound({"fade_in": 1, "fade_out": 1}).env, "time": time}

    assert evaluate(effect.gain, env) == pytest.approx(expected, abs=1e-9)
    assert evaluate(effect.gain, {**env, "sound": False}) == 1


def brightness(path: Path) -> np.ndarray:
    return decode_video_rgb(path, 64, 36).mean(axis=(1, 2, 3))


def make_clip(path: Path, offset: float = 0.0) -> Path:
    args = [
        "-f", "lavfi", "-i", "color=c=0xc8c8c8:size=64x36:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
        "-frames:v", "50", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
    ]
    if offset:
        args += ["-output_ts_offset", str(offset)]
    run_ffmpeg(*args, str(path))
    return path


def audio_levels(path: Path) -> np.ndarray:
    command = [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    samples = np.frombuffer(subprocess.run(command, capture_output=True, check=True).stdout, dtype=np.int16).astype(np.float64)
    return np.array([np.abs(chunk).mean() for chunk in np.array_split(samples, 8)])


@pytest.mark.parametrize("offset", [0.0, 0.5])
def test_a_real_clip_starts_and_ends_black_even_when_its_timestamps_start_late(tmp_path, ctx, need_ffmpeg, offset):
    clip = probe(make_clip(tmp_path / "grey.mp4", offset))

    result = run_chain([(effect_spec("fade"), {"fade_in": 0.4, "fade_out": 0.4})], [clip], ctx)[0]

    levels = brightness(result.path)
    assert levels[0] < 2 and levels[-1] < 2
    assert levels[25] > 180 and np.all(np.diff(levels[:12]) >= 0) and np.all(np.diff(levels[-12:]) <= 0)
    if not offset:
        assert len(levels) == 50 and levels[5] == pytest.approx(levels[25] / 2, abs=8)


def first_pts(path: Path, stream: str) -> float:
    probe_filter = ["-vf", "showinfo", "-frames:v", "1"] if stream == "v" else ["-af", "ashowinfo", "-frames:a", "1"]
    command = [find_ffmpeg(), "-hide_banner", "-nostdin", "-copyts", "-i", str(path), "-map", f"0:{stream}:0", *probe_filter, "-f", "null", "-"]
    found = re.search(r"pts_time:(-?[0-9.]+)", subprocess.run(command, capture_output=True, text=True, check=True).stderr)
    return float(found.group(1))


def make_late_picture_clip(path: Path) -> Path:
    run_ffmpeg(
        "-itsoffset", "0.5", "-f", "lavfi", "-i", "color=c=0xc8c8c8:size=64x36:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2.5",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    )
    return path


@pytest.mark.parametrize("sound", [False, True])
def test_picture_and_sound_keep_their_offset_when_the_picture_starts_late(tmp_path, ctx, need_ffmpeg, sound):
    clip = probe(make_late_picture_clip(tmp_path / "late.mkv"))
    offset = first_pts(clip.path, "v") - first_pts(clip.path, "a")
    assert offset > 0.4

    result = run_chain([(effect_spec("fade"), {"fade_in": 0.4, "fade_out": 0.4, "sound": sound})], [clip], ctx)[0]

    assert result.has_audio is True
    assert first_pts(result.path, "v") - first_pts(result.path, "a") == pytest.approx(offset, abs=0.05)
    assert brightness(result.path)[0] < 2


def test_the_sound_fades_with_the_picture_unless_switched_off(tmp_path, ctx, need_ffmpeg):
    clip = probe(make_clip(tmp_path / "tone.mp4"))

    faded = audio_levels(run_chain([(effect_spec("fade"), {"fade_in": 0.5, "fade_out": 0.5})], [clip], ctx)[0].path)
    kept = audio_levels(run_chain([(effect_spec("fade"), {"fade_in": 0.5, "fade_out": 0.5, "sound": False})], [clip], ctx)[0].path)

    assert faded[0] < kept[0] * 0.6 and faded[-1] < kept[-1] * 0.6
    assert faded[4] == pytest.approx(kept[4], rel=0.1)
