from __future__ import annotations

import numpy as np
import pytest

from src.platform.media_tools import FilterFragment, ToolCancelled, ToolContext, ToolFailed, ToolMedia, probe
from src.platform.media_tools.fastpath import RGB_IN, RGB_OUT, merge_fragments, run_fragments
from tests.platform.media_tools.media_factory import decode_video_rgb, make_video, video_frame_count

pytestmark = pytest.mark.usefixtures("need_ffmpeg")

FRAMES = 6
SIZE = 32


class Stage:
    def __init__(self, transform=None, record=None):
        self.transform = transform or (lambda frames: frames)
        self.record = record

    def apply(self, frames, indices, fps, duration):
        if self.record is not None:
            self.record.append((tuple(indices), fps, duration, frames.copy()))
        return self.transform(frames)


def stage(transform=None, record=None):
    return FilterFragment(stage=Stage(transform, record))


@pytest.fixture
def clip(tmp_path):
    return probe(make_video(tmp_path / "clip.mp4", seconds=0.6, size=(SIZE, SIZE), fps=10))


def indices_of(record):
    return [index for indices, *_ in record for index in indices]


def test_frames_reach_the_stage_in_order_with_their_index_and_rate(clip, ctx):
    record = []

    result = run_fragments(clip, [stage(record=record)], ctx)

    assert indices_of(record) == list(range(FRAMES)) == list(range(video_frame_count(result.path)))
    assert all(fps == pytest.approx(10.0) and duration == pytest.approx(clip.duration) for _, fps, duration, _ in record)
    frames = np.concatenate([batch for *_, batch in record])
    assert frames.shape == (FRAMES, SIZE, SIZE, 3) and frames.dtype == np.uint8
    assert not np.array_equal(frames[0], frames[-1])


def test_the_stage_decides_the_pixels_and_the_sound_is_kept(clip, ctx):
    black = run_fragments(clip, [stage(np.zeros_like)], ctx)

    assert probe(black.path).has_audio is True
    assert decode_video_rgb(black.path, SIZE, SIZE).max() < 20


def test_a_stage_after_a_trim_counts_frames_from_the_trim_start(clip, ctx):
    before, after = [], []
    fragments = [stage(record=before), FilterFragment(start=0.2, end=0.5), stage(record=after)]

    result = run_fragments(clip, fragments, ctx)

    assert indices_of(before) == list(range(FRAMES))
    assert indices_of(after) == [0, 1, 2]
    assert video_frame_count(result.path) == 3
    decode, middle, encode = ctx.commands[-3:]
    assert any(part.startswith("trim=start=0.200000") for part in middle[middle.index("-vf") + 1].split(","))
    assert any(part.startswith("atrim=start=0.200000") for part in encode[encode.index("-af") + 1].split(","))


def test_one_graph_when_every_step_is_ffmpeg_and_segments_when_a_stage_joins(clip):
    graph = merge_fragments(clip, [FilterFragment(vf=["hflip"], rgb=True), FilterFragment(vf=["vflip"])])
    assert graph.vf == [*RGB_IN, "hflip", "vflip"] and graph.rgb and graph.groups == []

    first, second = Stage(), Stage()
    mixed = merge_fragments(clip, [
        FilterFragment(vf=["crop=16:16:0:0"], width=16, height=16),
        FilterFragment(stage=first),
        FilterFragment(stage=second),
        FilterFragment(vf=["hflip"], rgb=True),
        FilterFragment(stage=Stage()),
    ])
    assert len(mixed.groups) == 2 and mixed.groups[0].stages == [first, second]
    assert [(group.width, group.height) for group in mixed.groups] == [(16, 16), (16, 16)]
    assert mixed.segments() == [["crop=16:16:0:0", *RGB_IN], ["hflip"], []]


def test_the_colour_space_is_converted_once_each_way_and_tagged(clip, ctx):
    result = run_fragments(clip, [FilterFragment(vf=["hflip"], rgb=True), FilterFragment(vf=["vflip"], rgb=True)], ctx)

    command = ctx.commands[-1]
    chain = command[command.index("-vf") + 1]
    assert chain.count("in_color_matrix") == 1 and chain.count("out_color_matrix") == 1
    assert chain.index(RGB_OUT) > chain.index("vflip")
    assert command[command.index("-colorspace") + 1] == "bt709"
    assert probe(result.path).has_audio is True


def test_two_stages_with_an_ffmpeg_step_between_run_three_processes_and_one_encode(clip, ctx):
    fragments = [stage(), FilterFragment(vf=["hflip"], rgb=True), stage()]

    result = run_fragments(clip, fragments, ctx)

    decode, middle, encode = ctx.commands[-3:]
    assert "pipe:1" in decode and "pipe:0" in middle and "pipe:1" in middle and "pipe:0" in encode
    assert "hflip" in middle[middle.index("-vf") + 1]
    assert sum("libx264" in command for command in ctx.commands) == 1
    assert video_frame_count(result.path) == FRAMES


def test_neighbouring_stages_share_one_batch_without_an_extra_process(clip, ctx):
    order = []

    def mark(name):
        def transform(frames):
            order.append(name)
            return frames
        return transform

    run_fragments(clip, [stage(mark("a")), stage(mark("b"))], ctx)

    assert len(ctx.commands) == 2
    assert order[:2] == ["a", "b"]


def test_audio_filters_after_a_stage_reach_the_encoder(clip, ctx):
    run_fragments(clip, [stage(), FilterFragment(af=["volume=0.5"])], ctx)

    encode = ctx.commands[-1]
    assert encode[encode.index("-af") + 1] == "volume=0.5"
    assert encode[encode.index("-map", encode.index("-map") + 1) + 1] == "1:a:0?"


def test_a_stage_that_resizes_a_frame_fails_and_leaves_nothing(clip, tmp_path):
    scratch = tmp_path / "scratch"

    with pytest.raises(ToolFailed):
        run_fragments(clip, [stage(lambda frames: frames[:, :16])], ToolContext(scratch))

    assert not list(scratch.glob("*.mp4"))


@pytest.mark.parametrize("middle", [False, True])
def test_a_stage_that_raises_stops_every_process_and_leaves_nothing(clip, tmp_path, middle):
    scratch = tmp_path / "scratch"

    def boom(frames):
        raise RuntimeError("effect crashed")

    fragments = [stage(boom), FilterFragment(vf=["hflip"], rgb=True), stage()] if middle else [stage(boom)]
    with pytest.raises(RuntimeError, match="effect crashed"):
        run_fragments(clip, fragments, ToolContext(scratch))

    assert not list(scratch.glob("*.mp4"))


@pytest.mark.parametrize("middle", [False, True])
def test_cancelling_midway_stops_at_the_next_batch_and_removes_the_partial_output(tmp_path, middle):
    long_clip = probe(make_video(tmp_path / "long.mp4", seconds=2.0, size=(SIZE, SIZE), fps=10))
    scratch = tmp_path / "scratch"
    calls = []
    ctx = ToolContext(scratch, is_cancelled=lambda: len(calls) >= 1)

    def transform(frames):
        calls.append(len(frames))
        return frames

    fragments = [stage(transform), FilterFragment(vf=["hflip"], rgb=True), stage()] if middle else [stage(transform)]
    with pytest.raises(ToolCancelled):
        run_fragments(long_clip, fragments, ctx)

    assert calls == [8] and not list(scratch.glob("*.mp4"))


def test_progress_counts_frames_at_the_encoder_and_never_goes_back(clip, tmp_path):
    seen = []
    ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append(fraction))

    run_fragments(clip, [stage()], ctx)

    assert seen == sorted(seen) and len(seen) == FRAMES and seen[-1] == pytest.approx(0.99)


def test_an_unreadable_video_is_a_tool_failure(tmp_path, ctx):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video at all")
    media = ToolMedia(path=broken, kind="video", width=32, height=32, fps=10.0, duration=1.0)

    with pytest.raises(ToolFailed):
        run_fragments(media, [stage()], ctx)

    assert not list((tmp_path / "scratch").glob("*.mp4"))


def test_the_source_audio_is_copied_when_nothing_touches_it(clip, ctx):
    run_fragments(clip, [stage()], ctx)

    decode, encode = ctx.commands[-2:]
    assert "rawvideo" in decode and "rawvideo" in encode
    assert encode[encode.index("-c:a") + 1] == "copy"
    assert encode[encode.index("-crf") + 1] == "18"
