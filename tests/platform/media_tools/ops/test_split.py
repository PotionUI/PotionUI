from __future__ import annotations

import sys

import pytest

from src.platform.media_tools import (
    InvalidToolParams,
    ToolCancelled,
    ToolContext,
    ToolMedia,
    UnsupportedMedia,
    probe,
    run_chain,
    validate_params,
)
from src.platform.media_tools.ops import split, trim
from src.platform.media_tools.ops.split import MAX_SPLIT_PARTS
from tests.platform.media_tools.media_factory import make_audio, make_image, make_video, video_frame_count
from tests.platform.media_tools.ops.time_helpers import files_under, slow_context

pytestmark = pytest.mark.usefixtures("need_ffmpeg")


@pytest.fixture
def audio(tmp_path):
    return probe(make_audio(tmp_path / "tone.wav", seconds=2.0))


def run_split(media, ctx, part_seconds):
    return run_chain([(split.spec(), {"part_seconds": part_seconds})], [media], ctx)


class TestRun:
    def test_splits_into_parts_of_the_requested_length(self, audio, ctx):
        parts = run_split(audio, ctx, 0.8)
        assert len(parts) == 3
        assert all(part.kind == "audio" for part in parts)
        assert [part.path.suffix for part in parts] == [".wav"] * 3
        for part in parts[:-1]:
            assert part.duration == pytest.approx(0.8, abs=0.1)

    def test_the_remainder_is_kept_as_a_shorter_last_part(self, audio, ctx):
        parts = run_split(audio, ctx, 0.8)
        assert parts[-1].duration < parts[0].duration
        assert parts[-1].duration == pytest.approx(0.4, abs=0.1)
        assert sum(part.duration for part in parts) == pytest.approx(2.0, abs=0.15)

    def test_parts_are_in_source_order_inside_the_scratch_dir(self, audio, ctx):
        parts = run_split(audio, ctx, 0.8)
        names = [part.path.name for part in parts]
        assert names == sorted(names)
        assert all(ctx.scratch_dir in part.path.parents for part in parts)

    def test_one_ffmpeg_pass_through_the_segment_muxer(self, audio, ctx):
        run_split(audio, ctx, 0.8)
        assert len(ctx.commands) == 1
        command = ctx.commands[0]
        assert command[command.index("-f") + 1] == "segment"
        assert command[command.index("-segment_time") + 1] == "0.800000"
        assert command[command.index("-reset_timestamps") + 1] == "1"
        assert "-vn" in command
        assert "copy" not in command

    def test_keeps_the_source_format(self, tmp_path, ctx):
        tone = probe(make_audio(tmp_path / "tone.mp3", seconds=2.0))
        parts = run_split(tone, ctx, 1.0)
        assert len(parts) >= 2
        assert {part.path.suffix for part in parts} == {".mp3"}

    def test_followed_by_a_step_it_runs_on_every_part(self, audio, ctx):
        steps = [
            (split.spec(), {"part_seconds": 0.8}),
            (trim.spec(), {"start": 0.0, "end": 0.25}),
        ]
        parts = run_chain(steps, [audio], ctx)
        assert len(parts) == 3
        for part in parts:
            assert part.duration == pytest.approx(0.25, abs=0.1)

    def test_reports_progress(self, audio, tmp_path):
        seen = []
        ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append(fraction))
        run_split(audio, ctx, 0.8)
        assert seen == sorted(seen)
        assert seen[-1] == 1.0


class TestRefusals:
    def test_part_length_must_be_positive(self):
        with pytest.raises(InvalidToolParams) as caught:
            validate_params(split.spec(), {"part_seconds": 0})
        assert caught.value.field == "part_seconds"

    def test_part_length_is_required(self):
        with pytest.raises(InvalidToolParams) as caught:
            validate_params(split.spec(), {})
        assert caught.value.field == "part_seconds"

    def test_part_not_shorter_than_the_media_is_refused(self, audio, ctx):
        with pytest.raises(InvalidToolParams) as caught:
            run_split(audio, ctx, 2.0)
        assert caught.value.field == "part_seconds"
        assert ctx.commands == []

    def test_too_many_parts_are_refused(self, tmp_path, ctx):
        long_audio = ToolMedia(path=tmp_path / "long.wav", kind="audio", duration=30.0, has_audio=True)
        with pytest.raises(InvalidToolParams) as caught:
            run_split(long_audio, ctx, 0.1)
        assert caught.value.field == "part_seconds"
        assert str(MAX_SPLIT_PARTS) in caught.value.message
        assert ctx.commands == []

    def test_exactly_the_part_limit_is_allowed(self, tmp_path, ctx):
        tone = make_audio(tmp_path / "tone.wav", seconds=20.0)
        parts = run_split(probe(tone), ctx, 0.1)
        assert len(parts) >= MAX_SPLIT_PARTS - 2

    def test_an_image_is_not_accepted(self, tmp_path, ctx):
        image = probe(make_image(tmp_path / "pic.png", (8, 8)))
        with pytest.raises(UnsupportedMedia):
            run_split(image, ctx, 0.5)


class TestCancel:
    def test_a_cancelled_context_stops_before_encoding(self, audio, tmp_path):
        ctx = ToolContext(tmp_path / "scratch", is_cancelled=lambda: True)
        with pytest.raises(ToolCancelled):
            run_split(audio, ctx, 0.8)
        assert files_under(ctx.scratch_dir) == []

    @pytest.mark.skipif(sys.platform == "win32", reason="uses a shell script as a stand-in encoder")
    def test_cancelling_a_running_split_removes_the_parts(self, tmp_path):
        ctx = slow_context(tmp_path)
        media = ToolMedia(path=tmp_path / "x.wav", kind="audio", duration=2.0, has_audio=True)
        with pytest.raises(ToolCancelled):
            run_split(media, ctx, 0.8)
        assert files_under(ctx.scratch_dir) == []
        assert not any(item.is_dir() for item in ctx.scratch_dir.iterdir())


def test_spec_is_one_to_many_audio():
    spec = split.spec()
    assert spec.cardinality == "one_to_many"
    assert spec.accepts == ("audio", "video")
    assert spec.produces == "same"


class TestAtATime:
    def test_a_video_splits_into_two_parts_whose_durations_add_up(self, tmp_path, ctx):
        video = probe(make_video(tmp_path / "clip.mp4", seconds=2.0, size=(32, 32), fps=10))
        first, second = run_chain([(split.spec(), {"mode": "at", "at": 0.8})], [video], ctx)
        assert (first.kind, second.kind) == ("video", "video")
        assert first.duration == pytest.approx(0.8, abs=0.11)
        assert second.duration == pytest.approx(1.2, abs=0.11)
        assert first.duration + second.duration == pytest.approx(2.0, abs=0.15)
        assert video_frame_count(first.path) + video_frame_count(second.path) == 20
        assert first.has_audio and second.has_audio

    def test_a_video_splits_every_few_seconds(self, tmp_path, ctx):
        video = probe(make_video(tmp_path / "clip.mp4", seconds=2.0, size=(32, 32), fps=10))
        parts = run_split(video, ctx, 0.8)
        assert len(parts) == 3
        assert sum(video_frame_count(part.path) for part in parts) == 20

    def test_audio_splits_at_a_time(self, audio, ctx):
        first, second = run_chain([(split.spec(), {"mode": "at", "at": 0.5})], [audio], ctx)
        assert first.duration == pytest.approx(0.5, abs=0.01)
        assert second.duration == pytest.approx(1.5, abs=0.01)
        assert [part.path.suffix for part in (first, second)] == [".wav", ".wav"]

    @pytest.mark.parametrize("at", [0.0, 2.0, 5.0])
    def test_a_point_outside_the_media_is_refused(self, audio, ctx, at):
        with pytest.raises(InvalidToolParams) as caught:
            run_chain([(split.spec(), {"mode": "at", "at": at})], [audio], ctx)
        assert caught.value.field == "at"

    def test_the_at_mode_needs_a_point(self):
        with pytest.raises(InvalidToolParams) as caught:
            validate_params(split.spec(), {"mode": "at"})
        assert caught.value.field == "at"
