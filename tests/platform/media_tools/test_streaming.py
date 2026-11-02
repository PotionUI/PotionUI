from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import ToolCancelled, ToolContext, ToolFailed, ToolMedia, probe
from src.platform.media_tools.streaming import stream_video_frames
from tests.platform.media_tools.media_factory import decode_video_rgb, make_video, video_frame_count

pytestmark = pytest.mark.usefixtures("need_ffmpeg")

FRAMES = 6


@pytest.fixture
def clip(tmp_path):
    return probe(make_video(tmp_path / "clip.mp4", seconds=0.6, size=(32, 32), fps=10))


def test_frames_are_handed_over_one_at_a_time_in_order(clip, ctx):
    seen = []

    def transform(frame):
        seen.append(frame.copy())
        return frame

    result = stream_video_frames(clip, ctx, transform)

    assert len(seen) == FRAMES == video_frame_count(result.path)
    assert all(frame.shape == (32, 32, 3) and frame.dtype == np.uint8 for frame in seen)
    assert not np.array_equal(seen[0], seen[-1])


def test_the_transform_decides_the_pixels(clip, ctx):
    black = stream_video_frames(clip, ctx, lambda frame: np.zeros_like(frame))

    assert probe(black.path).has_audio is True
    assert decode_video_rgb(black.path, 32, 32).max() < 20


def test_a_transform_that_resizes_a_frame_fails_and_leaves_nothing(clip, tmp_path):
    scratch = tmp_path / "scratch"

    with pytest.raises(ToolFailed):
        stream_video_frames(clip, ToolContext(scratch), lambda frame: frame[:16])

    assert not list(scratch.glob("*.mp4"))


def test_a_transform_that_raises_stops_both_ffmpeg_processes_and_leaves_nothing(clip, tmp_path):
    scratch = tmp_path / "scratch"

    def boom(frame):
        raise RuntimeError("effect crashed")

    with pytest.raises(RuntimeError):
        stream_video_frames(clip, ToolContext(scratch), boom)

    assert not list(scratch.glob("*.mp4"))


def test_cancelling_midway_removes_the_partial_output(clip, tmp_path):
    scratch = tmp_path / "scratch"
    calls = []
    ctx = ToolContext(scratch, is_cancelled=lambda: len(calls) >= 2)

    def transform(frame):
        calls.append(1)
        return frame

    with pytest.raises(ToolCancelled):
        stream_video_frames(clip, ctx, transform)

    assert len(calls) == 2 and not list(scratch.glob("*.mp4"))


def test_an_unreadable_video_is_a_tool_failure(tmp_path, ctx):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    media = ToolMedia(path=broken, kind="video", width=32, height=32, fps=10.0, duration=1.0)

    with pytest.raises(ToolFailed):
        stream_video_frames(media, ctx, lambda frame: frame)

    assert not list((tmp_path / "scratch").glob("*.mp4"))


def test_the_commands_are_recorded_on_the_runner(clip, ctx):
    stream_video_frames(clip, ctx, lambda frame: frame)

    decode, encode = ctx.commands[-2:]
    assert "rawvideo" in decode and "rawvideo" in encode
    assert encode[encode.index("-c:a") + 1] == "copy"
