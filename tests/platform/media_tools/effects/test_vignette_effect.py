from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import InvalidToolParams, ToolMedia, probe, run_chain, validate_params
from src.platform.media_tools.effects.runtime import EffectRuntime
from tests.platform.media_tools.effects.support import core_effect, effect_spec, numpy_only
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg

MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=1.0, fps=10.0)


def binding(params, media=MEDIA):
    spec = effect_spec("vignette")
    return spec.impl.bind(media, validate_params(spec, params))


def stage_for(params, runtime=None, media=MEDIA):
    spec = effect_spec("vignette", runtime or numpy_only())
    return spec.impl.video_filter(media, validate_params(spec, params)).stage


def flat(value=200, count=2, width=MEDIA.width, height=MEDIA.height):
    return np.full((count, height, width, 3), value, dtype=np.uint8)


def test_spec_is_a_texture_effect_for_video_and_images_with_three_short_params():
    spec = effect_spec("vignette")
    effect = core_effect("vignette")

    assert spec.group == "effects" and spec.accepts == ("video", "image") and effect.group == "texture"
    assert effect.time == "none" and effect.shader is not None
    assert [p["name"] for p in spec.params] == ["amount", "radius", "softness"]
    assert all(len(p["description"].split()) <= 5 for p in spec.params)
    assert validate_params(spec, {}) == {"amount": 40, "radius": 50, "softness": 50}


@pytest.mark.parametrize("params", [{"amount": 101}, {"radius": -1}, {"softness": 100.5}, {"strength": 40}])
def test_out_of_bounds_or_unknown_params_are_refused(params):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("vignette"), params)


@pytest.mark.parametrize(
    "params, derived",
    [
        ({}, {"depth": 0.4, "inner": 0.5, "outer": 1.0}),
        ({"amount": 100, "radius": 0, "softness": 100}, {"depth": 1.0, "inner": 0.0, "outer": 1.0}),
        ({"amount": 80, "radius": 70, "softness": 0}, {"depth": 0.8, "inner": 0.7, "outer": 0.71}),
    ],
)
def test_the_derive_turns_percentages_into_depth_and_a_soft_band(params, derived):
    assert binding(params).derived == pytest.approx(derived)


def test_it_renders_through_torch_then_numpy_and_never_through_ffmpeg():
    effect = core_effect("vignette")

    assert [candidate.kind for candidate in effect.render] == ["torch", "numpy"]
    assert stage_for({}, EffectRuntime(torch_available=lambda: True)).kind == "torch"
    assert stage_for({}).kind == "numpy"


def test_the_centre_keeps_its_value_and_the_corners_darken_by_the_amount():
    out = stage_for({"amount": 100, "radius": 0, "softness": 100}).apply(flat(), (0, 1), 10.0, 1.0)

    height, width = MEDIA.height, MEDIA.width
    centre = out[0, height // 2 - 1 : height // 2 + 1, width // 2 - 1 : width // 2 + 1]
    assert np.all(np.abs(centre.astype(int) - 200) <= 1)
    assert out[0, 0, 0].max() <= 3 and out[0, -1, -1].max() <= 3
    assert np.array_equal(out[0], out[1])


def test_the_falloff_grows_from_the_radius_outwards():
    out = stage_for({"amount": 60, "radius": 40, "softness": 50}).apply(flat(count=1, width=200, height=200), (0,), 10.0, 1.0)

    row = out[0, 100, 100:, 0].astype(int)
    assert row[0] == 200 and np.all(np.diff(row) <= 0)
    assert row[int(0.4 * np.hypot(100, 100)) - 1] == 200
    assert row[-1] < 200


def test_amount_zero_is_the_identity():
    frames = np.random.default_rng(3).integers(0, 256, size=(2, 36, 64, 3), dtype=np.uint8)

    assert np.array_equal(stage_for({"amount": 0}).apply(frames, (0, 1), 10.0, 1.0), frames)


def test_the_torch_render_equals_the_numpy_render():
    pytest.importorskip("torch")
    frames = np.random.default_rng(5).integers(0, 256, size=(3, 36, 64, 3), dtype=np.uint8)
    params = {"amount": 75, "radius": 30, "softness": 40}

    numpy_out = stage_for(params).apply(frames, (0, 1, 2), 10.0, 1.0)
    torch_out = stage_for(params, EffectRuntime(torch_available=lambda: True)).apply(frames, (0, 1, 2), 10.0, 1.0)

    assert np.abs(numpy_out.astype(int) - torch_out.astype(int)).max() <= 1


def test_images_get_the_vignette_and_keep_their_alpha():
    spec = effect_spec("vignette", numpy_only())
    image = Image.new("RGBA", (80, 60), (200, 200, 200, 128))

    out = spec.impl.image(image, validate_params(spec, {"amount": 100, "radius": 0, "softness": 100}))

    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 128
    assert out.getpixel((0, 0))[0] < 5 and abs(out.getpixel((40, 30))[0] - 200) <= 1


def test_a_real_clip_saves_with_dark_corners_and_its_sound(tmp_path, ctx, need_ffmpeg):
    path = tmp_path / "grey.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=0xc8c8c8:size=64x36:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1",
        "-frames:v", "10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    )
    spec = effect_spec("vignette", numpy_only())

    result = run_chain([(spec, {"amount": 100, "radius": 20, "softness": 60})], [probe(path)], ctx)[0]

    frames = decode_video_rgb(result.path, 64, 36)
    assert frames[:, 1:3, 1:3].mean() < 40 and frames[:, 16:20, 30:34].mean() > 180
    assert result.has_audio is True
