from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import InvalidToolParams, ToolContext, ToolFailed, ToolMedia, probe, run_chain, validate_params
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.template import AssetPath
from src.platform.media_tools.effects.tool import ASSETS_KEY
from src.platform.media_tools.timeline import Clock, StepContext
from tests.platform.media_tools.effects.support import core_effect, effect_spec
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg

FIXTURES = Path(core_effect("lut").folder) / "parity"
MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=1.0, fps=24.0)


@pytest.fixture
def luts(tmp_path):
    folder = tmp_path / "luts"
    shutil.copytree(FIXTURES, folder / "looks")
    return folder


def runtime_for(folder, ffmpeg=True):
    runtime = EffectRuntime(
        filters=(lambda: frozenset({"lut3d"})) if ffmpeg else frozenset, torch_available=lambda: False,
    )
    runtime.use_asset_dirs(lambda kind: [folder] if kind == "lut" else [])
    return runtime


def test_spec_is_a_colour_effect_with_a_lut_and_a_strength_from_0_to_1():
    spec = effect_spec("lut")
    effect = core_effect("lut")

    assert spec.accepts == ("video", "image") and effect.group == "colour" and effect.time == "none"
    assert [p["name"] for p in spec.params] == ["lut", "strength"]
    assert spec.params[1]["min"] == 0 and spec.params[1]["max"] == 1
    assert [(t.uniform, t.source, t.kind, t.mix) for t in effect.textures] == [("table", "lut", "lut3d", "amount")]
    assert [u["name"] for u in effect.uniforms()] == ["u_strength", "u_has_lut", "u_amount"]
    assert [c.kind for c in effect.render] == ["ffmpeg", "numpy"]


@pytest.mark.parametrize("ref", ["../escape.cube", "/etc/x.cube", "look.txt", "a/../b.cube", "x" * 300 + ".cube"])
def test_a_lut_ref_must_be_a_plain_cube_path(ref):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("lut"), {"lut": ref})


def test_refs_resolve_only_inside_the_lut_folders(tmp_path, luts):
    runtime = runtime_for(luts)
    (tmp_path / "outside.cube").write_text("LUT_3D_SIZE 2\n", encoding="utf-8")

    (luts / "linked.cube").symlink_to(luts / "looks" / "cool.cube")
    (luts / "escape.cube").symlink_to(tmp_path / "outside.cube")

    assert runtime.resolve_asset("lut", "looks/warm.cube") == (luts / "looks" / "warm.cube").resolve()
    assert runtime.resolve_asset("lut", "linked.cube") == (luts / "looks" / "cool.cube").resolve()
    assert runtime.resolve_asset("lut", "escape.cube") is None
    assert runtime.resolve_asset("lut", "../outside.cube") is None
    assert runtime.resolve_asset("lut", "looks/missing.cube") is None
    assert runtime.list_assets("lut", ".cube") == ["linked.cube", "looks/cool.cube", "looks/shaped.cube", "looks/warm.cube"]


def test_a_linked_lut_folder_works_like_a_linked_model_folder(tmp_path, luts):
    (tmp_path / "linked").symlink_to(luts, target_is_directory=True)
    runtime = runtime_for(tmp_path / "linked")

    assert runtime.resolve_asset("lut", "looks/warm.cube") == (luts / "looks" / "warm.cube").resolve()
    assert runtime.list_assets("lut", ".cube") == ["looks/cool.cube", "looks/shaped.cube", "looks/warm.cube"]


def test_a_missing_lut_fails_with_its_name(luts):
    spec = effect_spec("lut", runtime_for(luts))

    with pytest.raises(ToolFailed, match="looks/nope.cube is not installed"):
        spec.impl.video_filter(MEDIA, validate_params(spec, {"lut": "looks/nope.cube"}))


def test_no_lut_or_zero_strength_adds_no_filter(luts):
    spec = effect_spec("lut", runtime_for(luts))

    for params in ({}, {"lut": "looks/warm.cube", "strength": 0}):
        prepared = spec.impl.prepare(validate_params(spec, params), step(luts.parent))
        fragment = spec.impl.video_filter(MEDIA, prepared)
        assert fragment.vf == [] and fragment.stage is None


def step(scratch):
    ctx = ToolContext(Path(scratch) / "scratch")
    return StepContext(source=MEDIA, clock=Clock(length=1.0), ctx=ctx)


def test_prepare_stages_the_mixed_table_and_ffmpeg_reads_it_by_a_safe_relative_path(luts):
    spec = effect_spec("lut", runtime_for(luts))
    prepared = spec.impl.prepare(validate_params(spec, {"lut": "looks/warm.cube", "strength": 0.5}), step(luts.parent))

    relative = prepared[ASSETS_KEY]["lut"]
    fragment = spec.impl.video_filter(MEDIA, prepared)

    staged = (Path.cwd() / relative).resolve()
    assert staged.is_file() and staged.parent == (luts.parent / "scratch").resolve()
    assert staged.read_text().startswith("LUT_3D_SIZE 17\n")
    assert fragment.vf == [f"lut3d=file='{relative}':interp='trilinear'"] and fragment.rgb is True
    assert ASSETS_KEY not in spec.impl.public(prepared)


def test_without_a_staged_table_the_render_falls_back_to_numpy(luts):
    spec = effect_spec("lut", runtime_for(luts))

    fragment = spec.impl.video_filter(MEDIA, validate_params(spec, {"lut": "looks/warm.cube"}))

    assert fragment.vf == [] and fragment.stage.kind == "numpy"


def test_the_numpy_render_grades_and_strength_blends_towards_the_source(luts):
    spec = effect_spec("lut", runtime_for(luts, ffmpeg=False))
    frames = np.random.default_rng(1).integers(0, 256, size=(2, 36, 64, 3), dtype=np.uint8)

    full = spec.impl.video_filter(MEDIA, validate_params(spec, {"lut": "looks/cool.cube", "strength": 1})).stage
    half = spec.impl.video_filter(MEDIA, validate_params(spec, {"lut": "looks/cool.cube", "strength": 0.5})).stage

    graded = full.apply(frames, (0, 1), 24.0, 1.0).astype(int)
    blended = half.apply(frames, (0, 1), 24.0, 1.0).astype(int)
    assert np.abs(graded - frames).mean() > 5
    assert np.abs(blended - (frames + (graded - frames) / 2)).max() <= 2


def test_images_take_the_lut_through_numpy_and_keep_alpha(luts):
    spec = effect_spec("lut", runtime_for(luts))
    image = Image.new("RGBA", (16, 16), (120, 80, 40, 99))

    out = spec.impl.image(image, validate_params(spec, {"lut": "looks/warm.cube"}))

    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 99 and out.getpixel((0, 0))[:3] != (120, 80, 40)


def test_a_real_clip_is_graded_by_ffmpeg_like_the_numpy_render(tmp_path, luts, need_ffmpeg):
    path = tmp_path / "clip.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x36:rate=24", "-frames:v", "12", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv444p", "-crf", "0", str(path))
    clip = probe(path)
    params = {"lut": "looks/warm.cube"}

    graded = run_chain([(effect_spec("lut", runtime_for(luts)), params)], [clip], ToolContext(tmp_path / "a"))[0]
    reference = run_chain([(effect_spec("lut", runtime_for(luts, ffmpeg=False)), params)], [clip], ToolContext(tmp_path / "b"))[0]

    a, b = decode_video_rgb(graded.path, 64, 36), decode_video_rgb(reference.path, 64, 36)
    source = decode_video_rgb(path, 64, 36)
    means = [frames.mean(axis=(0, 1, 2)) for frames in (a, b, source)]
    assert np.abs(means[0] - means[1]).max() < 1.5
    assert np.abs(means[0] - means[2]).max() > 4
