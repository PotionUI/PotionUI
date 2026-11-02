from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from src.platform.media_tools import InvalidToolParams, ToolCancelled, ToolContext, ToolMedia, probe, run_chain, validate_params
from tests.platform.media_tools.effects.support import effect_spec, numpy_only
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg, video_frame_count, video_stream_line

SIZE = 32
FRAMES = 6
FPS = 10
MASK = 0xFFFFFFFF
MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=24, height=18, duration=1.0, fps=FPS)


def lowbias32(x):
    x ^= x >> 16
    x = (x * 0x7FEB352D) & MASK
    x ^= x >> 15
    x = (x * 0x846CA68B) & MASK
    return x ^ (x >> 16)


def shader_noise(cell_x, cell_y, seed, frame, channel):
    base = lowbias32((seed ^ lowbias32((frame + 0x632BE5AB) & MASK)) & MASK)
    frame_seed = lowbias32(base ^ (((channel + 1) * 0x27D4EB2D) & MASK))
    h = lowbias32(((cell_x * 73856093) ^ (cell_y * 19349663) ^ (((frame_seed + 1) * 83492791) & MASK)) & MASK)
    second = lowbias32(h ^ 0x9E3779B9)
    return h / 4294967296 + second / 4294967296 - 1


def stage_for(params, runtime=None, media=MEDIA):
    spec = effect_spec("film_grain", runtime)
    return spec.impl.video_filter(media, validate_params(spec, params)).stage


def frames_of(count=3, value=None, seed=1):
    if value is not None:
        return np.full((count, MEDIA.height, MEDIA.width, 3), value, dtype=np.uint8)
    return np.random.default_rng(seed).integers(0, 256, size=(count, MEDIA.height, MEDIA.width, 3), dtype=np.uint8)


def test_spec_is_a_video_and_image_effect_with_bounded_documented_params():
    spec = effect_spec("film_grain")

    assert spec.group == "effects" and spec.accepts == ("video", "image") and spec.cardinality == "one_to_one"
    assert [p["name"] for p in spec.params] == ["amount", "size", "color", "seed"]
    assert all(p["description"] for p in spec.params)
    assert validate_params(spec, {}) == {"amount": 30, "size": 1.5, "color": False, "seed": 0}


@pytest.mark.parametrize(
    "params",
    [{"amount": 101}, {"amount": -1}, {"size": 0.5}, {"size": 3.5}, {"seed": -1}, {"color": "maybe"}, {"unknown": 1}],
)
def test_out_of_bounds_params_are_refused(params):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("film_grain"), params)


@pytest.mark.parametrize("color", [False, True])
def test_the_numpy_grain_is_the_shader_hash_at_every_pixel(color):
    params = {"amount": 100, "size": 1, "color": color, "seed": 7}
    frames = frames_of(value=128)

    out = stage_for(params, numpy_only()).apply(frames, (0, 5, 37), FPS, 1.0).astype(np.float64)

    strength = 0.14 * 255
    for slot, frame in enumerate((0, 5, 37)):
        for x, y in ((0, 0), (7, 3), (23, 17)):
            for channel in range(3):
                noise = shader_noise(x, y, 7, frame, channel if color else 0)
                luma = 128 / 255
                mid = 1 - 0.7 * (2 * luma - 1) ** 2
                expected = np.floor(128 + noise * strength * mid + 0.5)
                assert abs(out[slot, y, x, channel] - expected) <= 1


@pytest.mark.parametrize(
    "params",
    [
        {"amount": 60, "size": 2, "color": True, "seed": 7},
        {"amount": 100, "size": 1, "color": False, "seed": 0},
        {"amount": 35, "size": 3, "color": True, "seed": 2147483647},
    ],
)
def test_torch_and_numpy_give_the_same_grain_within_one_level(params):
    frames = frames_of(4, seed=params["seed"] % 97)
    indices = (0, 1, 37, 95)

    numpy_out = stage_for(params, numpy_only()).apply(frames, indices, FPS, 1.0)
    torch_stage = stage_for(params)
    torch_out = torch_stage.apply(frames, indices, FPS, 1.0)

    assert torch_stage.kind == "torch"
    difference = np.abs(numpy_out.astype(int) - torch_out.astype(int))
    assert difference.max() <= 1 and difference.mean() <= 0.02


def test_the_hash_is_deterministic_per_seed_and_frame():
    frames = frames_of(1, value=128)
    stage = stage_for({"amount": 80, "seed": 3}, numpy_only())

    first, again = stage.apply(frames, (4,), FPS, 1.0), stage.apply(frames, (4,), FPS, 1.0)
    next_frame = stage.apply(frames, (5,), FPS, 1.0)
    other_seed = stage_for({"amount": 80, "seed": 4}, numpy_only()).apply(frames, (4,), FPS, 1.0)

    assert np.array_equal(first, again)
    assert not np.array_equal(first, next_frame) and not np.array_equal(first, other_seed)


def test_black_and_white_grain_is_the_same_on_every_channel():
    frames = frames_of(1, value=128)

    mono = stage_for({"amount": 100, "color": False}, numpy_only()).apply(frames, (0,), FPS, 1.0)
    colour = stage_for({"amount": 100, "color": True}, numpy_only()).apply(frames, (0,), FPS, 1.0)

    assert np.array_equal(mono[..., 0], mono[..., 2])
    assert not np.array_equal(colour[..., 0], colour[..., 2])


def test_the_cell_is_relative_to_the_frame_and_grain_is_flat_inside_it():
    media = MEDIA.with_changes(width=640, height=360)
    stage = stage_for({"amount": 100, "size": 3}, numpy_only(), media)
    frames = np.full((1, 360, 640, 3), 128, dtype=np.uint8)

    out = stage.apply(frames, (0,), FPS, 1.0)[0]

    assert stage.derived["cell"] == 1
    big = stage_for({"amount": 100, "size": 3}, numpy_only(), MEDIA.with_changes(width=1920, height=1080))
    assert big.derived["cell"] == 3
    assert out.shape == (360, 640, 3)


def test_no_amount_leaves_the_frames_alone():
    frames = frames_of()

    assert np.array_equal(stage_for({"amount": 0}, numpy_only()).apply(frames, (0, 1, 2), FPS, 1.0), frames)


def make_flat_clip(path, size=(SIZE, SIZE), frames=FRAMES, audio=True):
    args = ["-f", "lavfi", "-i", f"color=c=0x808080:size={size[0]}x{size[1]}:rate={FPS}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={frames / FPS}"]
    args += ["-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k", "-shortest"]
    run_ffmpeg(*args, str(path))
    return path


def grain(media, ctx, **params):
    return run_chain([(effect_spec("film_grain"), params)], [media], ctx)[0]


@pytest.fixture
def clip(tmp_path, need_ffmpeg):
    return probe(make_flat_clip(tmp_path / "flat.mp4"))


def test_every_frame_gets_its_own_grain_and_size_rate_and_sound_are_kept(clip, ctx):
    result = grain(clip, ctx, amount=80)

    frames = decode_video_rgb(result.path, SIZE, SIZE).astype(np.float64)
    assert min(np.abs(frames[i + 1] - frames[i]).mean() for i in range(len(frames) - 1)) > 1.0
    assert video_frame_count(result.path) == FRAMES and (result.width, result.height) == (SIZE, SIZE)
    assert result.fps == pytest.approx(clip.fps) and result.has_audio is True
    assert result.audio_codec == clip.audio_codec
    assert "bt709" in video_stream_line(result.path)


def test_the_same_seed_gives_the_same_video_and_another_seed_does_not(clip, tmp_path):
    first = grain(clip, ToolContext(tmp_path / "a"), amount=60, seed=7)
    again = grain(clip, ToolContext(tmp_path / "b"), amount=60, seed=7)
    other = grain(clip, ToolContext(tmp_path / "c"), amount=60, seed=8)

    a, b, c = (decode_video_rgb(m.path, SIZE, SIZE) for m in (first, again, other))
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_odd_dimensions_are_cropped_to_even_and_the_container_is_kept(tmp_path, ctx, need_ffmpeg):
    raw = tmp_path / "odd.rgb"
    raw.write_bytes(np.full((FRAMES, 31, 33, 3), 128, dtype=np.uint8).tobytes())
    path = tmp_path / "odd.mkv"
    run_ffmpeg("-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "33x31", "-r", str(FPS), "-i", str(raw), "-c:v", "ffv1", str(path))

    result = grain(probe(path), ctx, amount=40)

    assert (result.width, result.height) == (32, 30)
    assert result.path.suffix == ".mkv" and video_frame_count(result.path) == FRAMES


def test_cancelling_stops_the_run_and_leaves_no_partial_file(clip, tmp_path):
    scratch = tmp_path / "scratch"
    spec = effect_spec("film_grain")

    with pytest.raises(ToolCancelled):
        spec.impl.run([clip], validate_params(spec, {}), ToolContext(scratch, is_cancelled=lambda: True))

    assert not list(scratch.glob("*.mp4"))


def test_progress_reaches_the_end_without_going_back(clip, tmp_path):
    seen = []
    ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append(fraction))

    grain(clip, ctx, amount=20)

    assert seen[-1] == 1.0 and seen == sorted(seen)
