from types import SimpleNamespace

import pytest

from src.pipelines.pipes.cloud_generate.stitch import ClipInfo, StitchError, concat_command, probe_clip, stitch_clips
from src.platform.util.video_transcode import VideoProbe

FFMPEG = "/opt/ffmpeg/bin/ffmpeg"


def finished(returncode=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def ffmpeg_found():
    return FFMPEG


def no_ffmpeg():
    return None


def probed(**fields):
    values = dict(duration=4.0, width=1280, height=720, fps=24.0, video_codec="h264", has_audio=True, size_bytes=10)
    values.update(fields)
    return VideoProbe(**values)


def test_the_join_scales_every_shot_to_the_first_and_fills_missing_sound_with_silence(tmp_path):
    infos = [
        ClipInfo(width=1281, height=721, fps=24.0, duration=4.0, has_audio=True),
        ClipInfo(width=640, height=360, fps=30.0, duration=6.0, has_audio=False),
    ]

    command = concat_command(FFMPEG, ["a.mp4", "b.mp4"], infos, tmp_path / "film.mp4")

    graph = command[command.index("-filter_complex") + 1]
    assert command[:7] == [FFMPEG, "-y", "-loglevel", "error", "-i", "a.mp4", "-i"]
    assert ["-f", "lavfi", "-t", "6.000", "-i", "anullsrc=r=48000:cl=stereo"] == command[8:14]
    assert "[0:v]scale=1280:720" in graph and "[1:v]scale=1280:720" in graph and "fps=24" in graph
    assert "[0:a]aresample=48000" in graph and "[2:a]aresample=48000" in graph
    assert graph.endswith("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    assert command[-1] == str(tmp_path / "film.mp4") and "-map" in command and "[a]" in command


def test_a_film_without_any_sound_is_joined_without_an_audio_track(tmp_path):
    infos = [ClipInfo(640, 360, 24.0, 2.0, False), ClipInfo(640, 360, 24.0, 2.0, False)]

    command = concat_command(FFMPEG, ["a.mp4", "b.mp4"], infos, tmp_path / "film.mp4")

    graph = command[command.index("-filter_complex") + 1]
    assert "anullsrc" not in " ".join(command) and graph.endswith("[v0][v1]concat=n=2:v=1:a=0[v]")
    assert "[a]" not in command and "-c:a" not in command


def test_ffmpeg_joins_the_shots_when_it_is_available(tmp_path):
    calls = []
    out = tmp_path / "film.mp4"

    def run(command, **kwargs):
        calls.append(command)
        out.write_bytes(b"film")
        return finished()

    info = ClipInfo(64, 48, 24.0, 2.0, True)
    stitch_clips(["a.mp4", "b.mp4"], out, run=run, find=ffmpeg_found, probe=lambda clip: info)

    (command,) = calls
    assert command[0] == FFMPEG and out.read_bytes() == b"film"


def test_a_shot_that_cannot_be_probed_skips_ffmpeg(tmp_path):
    pytest.importorskip("cv2", reason="the fallback join reads clips with cv2", exc_type=ImportError)
    from src.features.cloud.testing.fake import fake_video_bytes

    clips = []
    for index in range(2):
        clip = tmp_path / f"{index}.mp4"
        clip.write_bytes(fake_video_bytes(1, start=(0, 0, 0), end=(255, 255, 255)))
        clips.append(clip)

    def run(command, **kwargs):
        raise AssertionError("ffmpeg must not run")

    out = stitch_clips(clips, tmp_path / "film.mp4", run=run, find=ffmpeg_found, probe=lambda clip: None)

    assert out.stat().st_size > 0


def test_without_ffmpeg_the_shots_are_joined_frame_by_frame_at_the_first_shots_size(tmp_path):
    cv2 = pytest.importorskip("cv2", reason="the fallback join reads clips with cv2", exc_type=ImportError)
    from src.features.cloud.testing.fake import fake_video_bytes

    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(fake_video_bytes(1, start=(255, 0, 0), end=(255, 0, 0), size=(32, 24)))
    second.write_bytes(fake_video_bytes(2, start=(0, 0, 255), end=(0, 0, 255), size=(48, 36)))

    out = stitch_clips([first, second], tmp_path / "film.mp4", find=no_ffmpeg)

    capture = cv2.VideoCapture(str(out))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    size = (int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    capture.release()
    assert (count, size) == (8 + 16, (32, 24))


def test_a_failing_ffmpeg_falls_back_to_the_frame_join(tmp_path):
    pytest.importorskip("cv2", reason="the fallback join reads clips with cv2", exc_type=ImportError)
    from src.features.cloud.testing.fake import fake_video_bytes

    clip = tmp_path / "only.mp4"
    clip.write_bytes(fake_video_bytes(1, start=(0, 0, 0), end=(9, 9, 9)))

    out = stitch_clips(
        [clip, clip], tmp_path / "film.mp4",
        run=lambda command, **kwargs: finished(returncode=1, stderr=b"boom"),
        find=ffmpeg_found, probe=lambda path: ClipInfo(32, 24, 8.0, 1.0, False),
    )

    assert out.stat().st_size > 0


def test_nothing_to_join_is_an_error(tmp_path):
    with pytest.raises(StitchError):
        stitch_clips([], tmp_path / "film.mp4")


def test_the_shared_probe_becomes_the_clip_size_rate_length_and_sound():
    info = probe_clip("x.mp4", probe=lambda path: probed(fps=29.97, duration=5.005))

    assert (info.width, info.height, info.fps, info.duration, info.has_audio) == (1280, 720, 29.97, 5.005, True)


def test_a_clip_without_a_usable_probe_is_not_joined_by_ffmpeg():
    assert probe_clip("x.mp4", probe=lambda path: None) is None
    assert probe_clip("x.mp4", probe=lambda path: probed(width=0)) is None
    assert probe_clip("x.mp4", probe=lambda path: probed(duration=0.0)) is None
    assert probe_clip("x.mp4", probe=lambda path: probed(fps=0.0)).fps == 24.0
