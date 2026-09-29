from pathlib import Path

import pytest
from PIL import Image

from src.features.content_safety.errors import ContentCheckUnavailable
from src.features.content_safety.gate import (
    ContentGate,
    build_frame_command,
    frame_times,
)
from tests.features.content_safety.fakes import FakeSettings, FakeTagger


def image():
    return Image.new("RGB", (4, 4))


def gate(tagger, **settings):
    return ContentGate(
        tagger,
        FakeSettings(**settings),
        frame_extractor=lambda path, count: [image() for _ in range(count)],
    )


def test_scores_are_questionable_plus_explicit_in_one_batched_call():
    tagger = FakeTagger([0.1, 0.9])

    scores = gate(tagger).rate_images([image(), image()])

    assert scores == pytest.approx([0.1, 0.9])
    assert tagger.calls == [2]


def test_video_verdict_is_the_max_over_the_sampled_frames():
    tagger = FakeTagger([0.1, 0.2, 0.95, 0.3, 0.1])

    assert gate(tagger).rate_video("clip.mp4") == pytest.approx(0.95)
    assert tagger.calls == [5]


def test_frame_count_follows_the_setting_and_is_capped():
    assert gate(FakeTagger(), content_video_sample_frames=3).frame_count() == 3
    assert gate(FakeTagger(), content_video_sample_frames=99).frame_count() == 8
    assert gate(FakeTagger(), content_video_sample_frames="junk").frame_count() == 5


def test_missing_weights_is_unavailable_and_never_calls_the_tagger():
    tagger = FakeTagger(present=False)

    with pytest.raises(ContentCheckUnavailable):
        gate(tagger).rate_images([image()])

    assert tagger.calls == []


def test_tagger_failure_becomes_unavailable():
    with pytest.raises(ContentCheckUnavailable):
        gate(FakeTagger(error=RuntimeError("oom"))).rate_images([image()])


def test_failed_frame_extraction_becomes_unavailable():
    def broken(path, count):
        raise OSError("ffmpeg missing")

    with pytest.raises(ContentCheckUnavailable):
        ContentGate(FakeTagger(), FakeSettings(), frame_extractor=broken).rate_video("clip.mp4")


def test_frames_are_sampled_from_ten_to_ninety_percent():
    assert frame_times(10.0, 5) == pytest.approx([1.0, 3.0, 5.0, 7.0, 9.0])
    assert frame_times(10.0, 1) == [5.0]


def test_one_ffmpeg_call_seeks_every_frame():
    command = build_frame_command("clip.mp4", [1.0, 2.0], Path("out"))

    assert command[0] == "ffmpeg"
    assert command.count("-i") == 2
    assert command.count("-ss") == 2
    assert Path("out", "frame_1.jpg").as_posix() in [Path(part).as_posix() for part in command]
