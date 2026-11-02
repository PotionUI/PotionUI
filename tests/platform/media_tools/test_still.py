from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import UnsupportedMedia, probe, run_chain
from src.platform.media_tools.ops import concat, crop, film_grain, fix_colors, mute, paint_layer, speed, trim, volume
from src.platform.media_tools.still import Clock, excerpt_steps, excerpt_window, read_frame, render_still
from tests.platform.media_tools.media_factory import decode_video_rgb, make_image, run_ffmpeg, video_frame_count

FPS = 10
SIZE = 32
GRAIN = {"amount": 60, "size": 1.0, "color": False, "seed": 5}


def make_clip(path, seconds=1.0, size=(SIZE, SIZE), tint=None, still=False):
    motion = "" if still else "+T*40"
    source = (
        f"color=c=gray:size={size[0]}x{size[1]}:rate={FPS},format=rgb24,"
        f"geq=r='40+X*4{motion}':g='60+Y*4':b='120+(X+Y)*2'"
    )
    if tint:
        source += f",colorchannelmixer={tint}"
    run_ffmpeg(
        "-f", "lavfi", "-i", source,
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
        "-frames:v", str(round(seconds * FPS)), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    )
    return probe(path)


def still_of(steps, media, time, ctx):
    image, snapped = read_frame(media, time, ctx)
    return np.asarray(render_still(steps, media, image, snapped, ctx).convert("RGB"), dtype=np.float64)


def rendered(steps, media, ctx):
    (result,) = run_chain(steps, [media], ctx)
    return decode_video_rgb(result.path, result.width, result.height)


def gap(first, second):
    return float(np.mean(np.abs(first - second)))


def likeness(first, second):
    return float(np.corrcoef(first.ravel(), second.ravel())[0, 1])


class TestClock:
    def test_trim_then_speed_maps_output_time_back_to_the_source(self):
        clock = Clock(length=10.0).trimmed(2.0, 8.0).sped(2.0)
        assert clock.length == pytest.approx(3.0)
        assert clock.source_time(1.0) == pytest.approx(4.0)
        assert clock.output_time(4.0) == pytest.approx(1.0)
        assert clock.source_window() == (2.0, pytest.approx(8.0))

    def test_a_trim_past_the_end_is_clamped_to_the_length(self):
        assert Clock(length=5.0).trimmed(1.0, 9.0).length == pytest.approx(4.0)


@pytest.mark.usefixtures("need_ffmpeg")
class TestStillMatchesTheRender:
    def test_grain_on_a_frame_matches_that_frame_of_the_full_render(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4", still=True)
        steps = [(film_grain.spec(), GRAIN)]
        frames = rendered(steps, media, ctx)
        plain = still_of([], media, 0.4, ctx)
        grain = still_of(steps, media, 0.4, ctx) - plain
        assert likeness(grain, frames[4] - plain) > 0.8
        assert likeness(grain, frames[5] - plain) < 0.3
        assert gap(grain + plain, frames[4]) < 4.0

    def test_grain_after_a_trim_uses_the_trimmed_frame_numbering(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4", still=True)
        steps = [(trim.spec(), {"start": 0.3, "end": 0.9}), (film_grain.spec(), GRAIN)]
        frames = rendered(steps, media, ctx)
        plain = still_of([], media, 0.6, ctx)
        grain = still_of(steps, media, 0.6, ctx) - plain
        assert likeness(grain, frames[3] - plain) > 0.8
        assert max(likeness(grain, frames[index] - plain) for index in (2, 4)) < 0.3

    def test_fix_colours_on_a_frame_matches_the_full_render(self, tmp_path, ctx):
        media = make_clip(tmp_path / "cast.mp4", tint="rr=0.6:gg=0.9:bb=1.0")
        steps = [(fix_colors.spec(), {"white_balance": True, "contrast": True, "strength": 100})]
        frames = rendered(steps, media, ctx)
        still = still_of(steps, media, 0.5, ctx)
        source = still_of([], media, 0.5, ctx)
        assert gap(still, frames[5]) < 3.0
        assert gap(source, frames[5]) > 10 * gap(still, frames[5])

    def test_a_crop_then_grain_matches_in_size_and_look(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4")
        steps = [(crop.spec(), {"x": 8, "y": 8, "width": 16, "height": 16}), (film_grain.spec(), GRAIN)]
        frames = rendered(steps, media, ctx)
        still = still_of(steps, media, 0.2, ctx)
        assert still.shape == frames[2].shape == (16, 16, 3)
        assert gap(still, frames[2]) < 4.0

    def test_sound_and_timing_steps_leave_the_picture_alone(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4")
        plain = still_of([], media, 0.5, ctx)
        steps = [
            (trim.spec(), {"start": 0.0, "end": 0.9}),
            (speed.spec(), {"speed": 2}),
            (volume.spec(), {"percent": 50}),
            (mute.spec(), {}),
        ]
        assert np.array_equal(still_of(steps, media, 0.5, ctx), plain)


@pytest.mark.usefixtures("need_ffmpeg")
class TestStillRefusals:
    def test_a_tool_without_a_frame_path_names_its_step(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4")
        with pytest.raises(UnsupportedMedia) as caught:
            still_of([(mute.spec(), {}), (concat.spec(), {})], media, 0.1, ctx)
        assert caught.value.step_index == 1

    def test_a_requirement_broken_by_an_earlier_step_is_refused(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4")
        with pytest.raises(UnsupportedMedia) as caught:
            still_of([(mute.spec(), {}), (volume.spec(), {"percent": 50})], media, 0.1, ctx)
        assert caught.value.step_index == 1

    def test_the_frame_index_is_snapped_and_clamped_to_the_clip(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4")
        assert read_frame(media, 0.44, ctx)[1] == pytest.approx(0.4)
        assert read_frame(media, 99.0, ctx)[1] == pytest.approx(0.9)


def test_an_image_takes_its_paint_layer_on_top(tmp_path, ctx):
    source = probe(make_image(tmp_path / "pic.png", (20, 10), color=(10, 20, 30)))
    layer = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    layer.paste((250, 0, 0, 255), (0, 0, 20, 20))
    layer.save(tmp_path / "layer.png")
    steps = [(paint_layer.spec(), {"layer": probe(tmp_path / "layer.png")})]
    image, _ = read_frame(source, 0.0, ctx)
    result = np.asarray(render_still(steps, source, image, 0.0, ctx))
    assert result.shape == (10, 20, 3)
    assert tuple(result[5, 2]) == (250, 0, 0)
    assert tuple(result[5, 17]) == (10, 20, 30)


@pytest.mark.usefixtures("need_ffmpeg")
class TestExcerpt:
    def test_the_window_follows_trim_and_speed(self, tmp_path):
        media = make_clip(tmp_path / "clip.mp4", seconds=4.0)
        steps = [(trim.spec(), {"start": 1.0, "end": 4.0}), (speed.spec(), {"speed": 2})]
        assert excerpt_window(steps, media, 2.0, 1.0) == (pytest.approx(2.0), pytest.approx(4.0))
        assert excerpt_window(steps, media, 0.0, 1.0) == (pytest.approx(1.0), pytest.approx(3.0))
        assert excerpt_window(steps, media, 3.9, 1.0) == (pytest.approx(2.0), pytest.approx(4.0))

    def test_a_short_preview_has_the_asked_length_and_height(self, tmp_path, ctx):
        media = make_clip(tmp_path / "clip.mp4", seconds=3.0, size=(64, 48))
        steps = [(trim.spec(), {"start": 0.5, "end": 3.0}), (film_grain.spec(), GRAIN)]
        plan = excerpt_steps(steps, media, 1.0, 1.0, 24, 28)
        assert [spec.id for spec, _ in plan] == ["preview_excerpt", "film_grain", "preview_scale"]
        (result,) = run_chain(plan, [media], ctx, validate=False)
        assert result.path.suffix == ".mp4"
        assert (result.width, result.height) == (32, 24)
        assert result.duration == pytest.approx(1.0, abs=0.11)
        assert video_frame_count(result.path) == 10
        assert result.has_audio

    def test_a_split_in_the_stack_is_left_out_of_the_preview(self, tmp_path):
        from src.platform.media_tools.ops import split

        media = make_clip(tmp_path / "clip.mp4", seconds=2.0)
        plan = excerpt_steps([(split.spec(), {"mode": "at", "at": 1.0})], media, 0.0, 1.0, 480, 28)
        assert [spec.id for spec, _ in plan] == ["preview_excerpt", "preview_scale"]
