from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from src.platform.media_tools import InvalidToolParams, ToolMedia, probe, run_chain, validate_params
from src.platform.media_tools.effects.template import MAX_STEP_CHARS
from src.platform.media_tools.effects.tool import VIDEO_TIME_RESET
from tests.platform.media_tools.effects.support import core_effect, effect_spec, numpy_only
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg

MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=4.0, fps=24.0)
CORNER = re.compile(r"(x[0-3]|y[0-3])='([^']*)'")


def bound(params, media=MEDIA):
    spec = effect_spec("ken_burns")
    return spec.impl.bind(media, validate_params(spec, params))


def corners(params, frame, media=MEDIA):
    spec = effect_spec("ken_burns")
    vf = spec.impl.video_filter(media, validate_params(spec, params)).vf
    perspective = next(item for item in vf if item.startswith("perspective="))
    values = {"W": media.width, "H": media.height, "frame_in": frame + 1}
    out = {}
    for name, text in CORNER.findall(perspective):
        expression = re.sub(r"\bin\b", "frame_in", text).replace("if(", "_if(").replace("clip(", "_clip(")
        out[name] = eval(expression, {"_if": lambda c, a, b: a if c else b, "_clip": lambda v, lo, hi: min(max(v, lo), hi)}, values)
    return out


def test_spec_is_a_motion_effect_with_zoom_anchors_and_easing():
    spec = effect_spec("ken_burns")
    effect = core_effect("ken_burns")

    assert spec.accepts == ("video",) and effect.group == "motion" and effect.time == "seconds"
    assert [p["name"] for p in spec.params] == ["zoom_from", "zoom_to", "pan_from", "pan_to", "easing"]
    assert all(len(p["description"].split()) <= 6 for p in spec.params)
    assert validate_params(spec, {}) == {
        "zoom_from": 1, "zoom_to": 1.25, "pan_from": "center", "pan_to": "center", "easing": "smooth",
    }
    assert [candidate.kind for candidate in effect.render] == ["ffmpeg", "numpy"]


@pytest.mark.parametrize("params", [{"zoom_from": 0.9}, {"zoom_to": 3.5}, {"pan_to": "middle"}, {"easing": "bounce"}])
def test_out_of_bounds_params_are_refused(params):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("ken_burns"), params)


def test_the_derive_reads_the_anchors_and_spans_first_to_last_frame():
    derived = bound({"pan_from": "top_left", "pan_to": "bottom_right", "easing": "linear"}).derived

    assert derived == {"smooth": False, "span": 95, "fx": 0.0, "fy": 0.0, "tx": 1.0, "ty": 1.0}
    assert bound({}).derived["smooth"] is True


def test_the_perspective_template_is_bounded_and_time_reset():
    spec = effect_spec("ken_burns")
    vf = spec.impl.video_filter(MEDIA, validate_params(spec, {"zoom_to": 2})).vf

    assert vf[0] == VIDEO_TIME_RESET and vf[1].startswith("perspective=") and len(vf) == 2
    assert "interpolation='linear'" in vf[1] and "eval='frame'" in vf[1]
    assert len(vf[1]) < MAX_STEP_CHARS


def test_the_first_frame_shows_the_start_window_and_the_last_frame_the_end_window():
    params = {"zoom_from": 1, "zoom_to": 2, "pan_from": "center", "pan_to": "top_left"}

    first = corners(params, 0)
    last = corners(params, 95)

    assert first == pytest.approx({"x0": 0, "y0": 0, "x1": 64, "y1": 0, "x2": 0, "y2": 36, "x3": 64, "y3": 36})
    assert last == pytest.approx({"x0": 0, "y0": 0, "x1": 32, "y1": 0, "x2": 0, "y2": 18, "x3": 32, "y3": 18})


def test_smooth_easing_starts_slower_than_linear():
    smooth = corners({"zoom_to": 2, "easing": "smooth"}, 10)
    linear = corners({"zoom_to": 2, "easing": "linear"}, 10)

    assert 0 < smooth["x0"] < linear["x0"]


def test_the_numpy_render_matches_the_window_and_is_the_identity_at_zoom_one():
    spec = effect_spec("ken_burns", numpy_only())
    frames = np.random.default_rng(4).integers(0, 256, size=(2, 36, 64, 3), dtype=np.uint8)

    still = spec.impl.video_filter(MEDIA, validate_params(spec, {"zoom_from": 1, "zoom_to": 1})).stage
    zoom = spec.impl.video_filter(MEDIA, validate_params(spec, {"zoom_from": 2, "zoom_to": 2, "pan_from": "top_left", "pan_to": "top_left"})).stage

    assert np.array_equal(still.apply(frames, (0, 50), 24.0, 4.0), frames)
    out = zoom.apply(frames, (0, 50), 24.0, 4.0)
    assert np.array_equal(out[0, ::2, ::2], frames[0, :18, :32])


def test_a_real_clip_zooms_into_the_chosen_corner(tmp_path, ctx, need_ffmpeg):
    path = tmp_path / "quad.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=white:size=64x36:rate=24",
        "-vf", "drawbox=x=0:y=0:w=32:h=18:color=black:t=fill",
        "-frames:v", "24", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    )

    result = run_chain([(effect_spec("ken_burns"), {"zoom_from": 1, "zoom_to": 2, "pan_to": "top_left"})], [probe(path)], ctx)[0]

    frames = decode_video_rgb(result.path, 64, 36)
    assert len(frames) == 24
    assert frames[0].mean() == pytest.approx(255 * 0.75, abs=12)
    assert frames[-1][4:-4, 4:-4].mean() < 30
