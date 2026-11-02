from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import (
    InvalidToolParams,
    ToolCancelled,
    ToolContext,
    probe,
    run_chain,
    validate_params,
)
from src.platform.media_tools.ops import film_grain
from tests.platform.media_tools.media_factory import decode_video_rgb, run_ffmpeg, video_frame_count, video_stream_line

pytestmark = pytest.mark.usefixtures("need_ffmpeg")

SIZE = 32
FRAMES = 6
FPS = 10


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
    return run_chain([(film_grain.spec(), params)], [media], ctx)[0]


@pytest.fixture
def clip(tmp_path):
    return probe(make_flat_clip(tmp_path / "flat.mp4"))


def test_spec_is_a_video_effect_with_bounded_documented_params():
    spec = film_grain.spec()

    assert spec.group == "effects" and spec.accepts == ("video",) and spec.cardinality == "one_to_one"
    assert [p["name"] for p in spec.params] == ["amount", "size", "color", "seed"]
    assert all(p["description"] for p in spec.params)
    resolved = validate_params(spec, {})
    assert resolved == {"amount": 30, "size": 1.5, "color": True, "seed": 0}


@pytest.mark.parametrize(
    "params",
    [{"amount": 101}, {"amount": -1}, {"size": 0.5}, {"size": 3.5}, {"seed": -1}, {"color": "maybe"}, {"unknown": 1}],
)
def test_out_of_bounds_params_are_refused(params):
    with pytest.raises(InvalidToolParams):
        validate_params(film_grain.spec(), params)


def test_every_frame_gets_its_own_grain(clip, ctx):
    result = grain(clip, ctx, amount=80)

    frames = decode_video_rgb(result.path, SIZE, SIZE)
    steps = [np.abs(frames[i + 1] - frames[i]).mean() for i in range(len(frames) - 1)]
    assert min(steps) > 1.0


def test_without_grain_the_static_clip_stays_static(clip, ctx):
    result = grain(clip, ctx, amount=0)

    frames = decode_video_rgb(result.path, SIZE, SIZE)
    assert max(np.abs(frames[i + 1] - frames[i]).mean() for i in range(len(frames) - 1)) < 0.5


def test_frame_count_fps_size_and_sound_are_kept(clip, ctx):
    result = grain(clip, ctx, amount=50)

    assert video_frame_count(result.path) == FRAMES
    assert (result.width, result.height) == (SIZE, SIZE)
    assert result.fps == pytest.approx(clip.fps)
    assert result.has_audio is True
    assert result.audio_codec == clip.audio_codec
    assert result.duration == pytest.approx(clip.duration, abs=0.15)


def test_the_colour_space_is_tagged_so_players_decode_the_same_colours(clip, ctx):
    result = grain(clip, ctx, amount=30)

    line = video_stream_line(result.path)
    assert "yuv420p" in line and "bt709" in line


def test_a_clip_without_sound_stays_silent(tmp_path, ctx):
    media = probe(make_flat_clip(tmp_path / "mute.mp4", audio=False))

    result = grain(media, ctx, amount=50)

    assert result.has_audio is False and video_frame_count(result.path) == FRAMES


def test_the_same_seed_gives_the_same_video_and_another_seed_does_not(clip, tmp_path):
    first = grain(clip, ToolContext(tmp_path / "a"), amount=60, seed=7)
    again = grain(clip, ToolContext(tmp_path / "b"), amount=60, seed=7)
    other = grain(clip, ToolContext(tmp_path / "c"), amount=60, seed=8)

    a, b, c = (decode_video_rgb(m.path, SIZE, SIZE) for m in (first, again, other))
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_zero_amount_matches_the_input_within_codec_tolerance(tmp_path, ctx):
    path = tmp_path / "colours.mp4"
    run_ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size={SIZE}x{SIZE}:rate={FPS}", "-frames:v", str(FRAMES),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(path),
    )
    media = probe(path)

    result = grain(media, ctx, amount=0)

    before, after = decode_video_rgb(path, SIZE, SIZE), decode_video_rgb(result.path, SIZE, SIZE)
    assert before.shape == after.shape
    assert np.abs(before - after).mean() < 4.0


def test_amount_adds_visible_grain(clip, ctx):
    result = grain(clip, ctx, amount=100)

    frames = decode_video_rgb(result.path, SIZE, SIZE)
    assert frames.std(axis=0).mean() > 2.0


def test_black_and_white_grain_is_the_same_on_every_channel(clip, tmp_path):
    mono = decode_video_rgb(grain(clip, ToolContext(tmp_path / "m"), amount=100, color=False).path, SIZE, SIZE)
    colour = decode_video_rgb(grain(clip, ToolContext(tmp_path / "c"), amount=100, color=True).path, SIZE, SIZE)

    def channel_agreement(frames):
        deviation = frames - frames.mean(axis=(1, 2), keepdims=True)
        return np.corrcoef(deviation[..., 0].ravel(), deviation[..., 2].ravel())[0, 1]

    assert channel_agreement(mono) > 0.9
    assert channel_agreement(colour) < 0.5


def test_a_larger_size_makes_a_coarser_grain(clip, tmp_path):
    def neighbour_agreement(size):
        frames = decode_video_rgb(grain(clip, ToolContext(tmp_path / f"s{size}"), amount=100, size=size).path, SIZE, SIZE)
        luma = frames.mean(axis=-1)
        deviation = luma - luma.mean(axis=(1, 2), keepdims=True)
        return np.corrcoef(deviation[:, :, :-1].ravel(), deviation[:, :, 1:].ravel())[0, 1]

    assert neighbour_agreement(3.0) > neighbour_agreement(1.0) + 0.2


def test_odd_dimensions_are_cropped_to_even(tmp_path, ctx):
    raw = tmp_path / "odd.rgb"
    raw.write_bytes(np.full((FRAMES, 31, 33, 3), 128, dtype=np.uint8).tobytes())
    path = tmp_path / "odd.mkv"
    run_ffmpeg(
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "33x31", "-r", str(FPS), "-i", str(raw),
        "-c:v", "ffv1", str(path),
    )
    odd = probe(path)

    result = grain(odd, ctx, amount=40)

    assert (odd.width, odd.height) == (33, 31)
    assert (result.width, result.height) == (32, 30)
    assert result.path.suffix == ".mkv" and video_frame_count(result.path) == FRAMES


def test_the_container_of_the_source_is_kept(tmp_path, ctx):
    path = tmp_path / "flat.webm"
    run_ffmpeg(
        "-f", "lavfi", "-i", f"color=c=0x808080:size={SIZE}x{SIZE}:rate={FPS}", "-frames:v", str(FRAMES),
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", str(path),
    )

    result = grain(probe(path), ctx, amount=40)

    assert result.path.suffix == ".webm" and video_frame_count(result.path) == FRAMES


def test_cancelling_stops_the_run_and_leaves_no_partial_file(clip, tmp_path):
    scratch = tmp_path / "scratch"
    ctx = ToolContext(scratch, is_cancelled=lambda: True)

    with pytest.raises(ToolCancelled):
        film_grain.spec().impl.run([clip], validate_params(film_grain.spec(), {}), ctx)

    assert not list(scratch.glob("*.mp4"))


def test_progress_reaches_the_end(clip, tmp_path):
    seen = []
    ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append(fraction))

    grain(clip, ctx, amount=20)

    assert seen[-1] == 1.0 and seen == sorted(seen)
