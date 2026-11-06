from __future__ import annotations

import subprocess

import numpy as np
import pytest

from src.platform.media_tools import (
    InvalidToolParams,
    ToolContext,
    UnsupportedMedia,
    probe,
    run_chain,
    validate_params,
)
from src.platform.media_tools.ops import mute, replace_audio, trim, volume
from src.platform.util.video_transcode import find_ffmpeg
from tests.platform.media_tools.media_factory import make_audio, make_video, video_frame_count

pytestmark = pytest.mark.usefixtures("need_ffmpeg")

RATE = 22050


def samples(path, start=None, end=None):
    command = [find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:a:0"]
    command += ["-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"]
    data = np.frombuffer(subprocess.run(command, capture_output=True, check=True).stdout, dtype=np.float32)
    first = 0 if start is None else int(start * RATE)
    last = len(data) if end is None else int(end * RATE)
    return data[first:last]


def rms(values):
    return float(np.sqrt(np.mean(np.square(values.astype(np.float64)))))


def dominant_hz(values):
    spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values))))
    return float(np.fft.rfftfreq(len(values), 1 / RATE)[int(np.argmax(spectrum))])


def run(spec, media, ctx, **params):
    return run_chain([(spec, params)], [media], ctx)


@pytest.fixture
def clip(tmp_path):
    return probe(make_video(tmp_path / "clip.mp4", seconds=2.0, size=(32, 32), fps=10))


@pytest.fixture
def tone(tmp_path):
    return probe(make_audio(tmp_path / "tone.wav", seconds=2.0))


class TestMute:
    def test_the_result_has_no_audio_stream(self, clip, ctx):
        (result,) = run(mute.spec(), clip, ctx)
        assert result.has_audio is False
        assert result.duration == pytest.approx(clip.duration, abs=0.05)
        assert video_frame_count(result.path) == 20

    def test_the_video_stream_is_copied_not_encoded(self, clip, ctx):
        run(mute.spec(), clip, ctx)
        command = ctx.commands[-1]
        assert command[command.index("-c:v") + 1] == "copy"
        assert "-an" in command and "libx264" not in command

    def test_a_silent_video_is_refused(self, tmp_path, ctx):
        silent = probe(make_video(tmp_path / "silent.mp4", seconds=1.0, audio=False))
        with pytest.raises(UnsupportedMedia):
            run(mute.spec(), silent, ctx)

    def test_after_another_edit_it_shares_one_encode(self, clip, ctx):
        (result,) = run_chain([(trim.spec(), {"start": 0.5, "end": 1.5}), (mute.spec(), {})], [clip], ctx)
        assert len(ctx.commands) == 1
        assert result.has_audio is False
        assert result.duration == pytest.approx(1.0, abs=0.11)


class TestVolume:
    def test_half_the_level_halves_the_amplitude(self, tone, ctx):
        (result,) = run(volume.spec(), tone, ctx, percent=50)
        assert rms(samples(result.path)) / rms(samples(tone.path)) == pytest.approx(0.5, rel=0.02)

    def test_decibels_scale_the_amplitude(self, tone, ctx):
        (result,) = run(volume.spec(), tone, ctx, unit="db", db=-6.0206)
        assert rms(samples(result.path)) / rms(samples(tone.path)) == pytest.approx(0.5, rel=0.02)

    def test_a_video_keeps_its_picture_and_scales_its_sound(self, clip, ctx):
        (result,) = run(volume.spec(), clip, ctx, percent=200)
        command = ctx.commands[-1]
        assert command[command.index("-c:v") + 1] == "copy"
        assert video_frame_count(result.path) == 20
        ratio = rms(samples(result.path, 0.3, 1.7)) / rms(samples(clip.path, 0.3, 1.7))
        assert ratio == pytest.approx(2.0, rel=0.08)

    def test_a_fade_in_ramps_up_and_a_fade_out_ramps_down(self, tone, ctx):
        (result,) = run(volume.spec(), tone, ctx, fade_in=0.5, fade_out=0.5)
        rising = [rms(samples(result.path, at, at + 0.1)) for at in (0.0, 0.1, 0.2, 0.3, 0.4)]
        falling = [rms(samples(result.path, at, at + 0.1)) for at in (1.5, 1.6, 1.7, 1.8, 1.9)]
        middle = rms(samples(result.path, 0.8, 1.2))
        assert rising == sorted(rising) and rising[0] < 0.25 * middle
        assert falling == sorted(falling, reverse=True) and falling[-1] < 0.25 * middle
        assert middle == pytest.approx(rms(samples(tone.path, 0.8, 1.2)), rel=0.02)

    @pytest.mark.parametrize("params", [{"percent": 401}, {"percent": -1}, {"unit": "db", "db": 21}, {"fade_in": -1}])
    def test_out_of_range_params_are_refused(self, params):
        with pytest.raises(InvalidToolParams):
            validate_params(volume.spec(), params)


class TestReplaceAudio:
    def test_the_new_track_replaces_the_sound_and_the_length_stays(self, clip, tmp_path, ctx):
        track = probe(make_audio(tmp_path / "high.wav", seconds=3.0, freq=1000))
        (result,) = run(replace_audio.spec(), clip, ctx, audio=track)
        assert result.duration == pytest.approx(clip.duration, abs=0.1)
        assert dominant_hz(samples(result.path, 0.2, 1.2)) == pytest.approx(1000, abs=30)
        assert len(samples(result.path)) / RATE == pytest.approx(2.0, abs=0.1)
        command = ctx.commands[-1]
        assert command[command.index("-c:v") + 1] == "copy"

    def test_a_short_track_loops_to_fill_the_video(self, clip, tmp_path, ctx):
        track = probe(make_audio(tmp_path / "short.wav", seconds=0.6, freq=1000))
        (result,) = run(replace_audio.spec(), clip, ctx, audio=track)
        assert len(samples(result.path)) / RATE == pytest.approx(2.0, abs=0.1)
        assert rms(samples(result.path, 1.5, 1.9)) == pytest.approx(rms(samples(track.path)), rel=0.1)

    def test_without_looping_a_short_track_ends_early(self, clip, tmp_path, ctx):
        track = probe(make_audio(tmp_path / "short.wav", seconds=0.6, freq=1000))
        (result,) = run(replace_audio.spec(), clip, ctx, audio=track, loop=False)
        assert result.duration == pytest.approx(clip.duration, abs=0.1)
        assert len(samples(result.path)) / RATE == pytest.approx(0.6, abs=0.1)

    def test_the_start_offset_skips_into_the_track(self, clip, tmp_path, ctx):
        track = probe(make_audio(tmp_path / "track.wav", seconds=1.0, freq=1000))
        (result,) = run(replace_audio.spec(), clip, ctx, audio=track, start=0.5, loop=False)
        assert len(samples(result.path)) / RATE == pytest.approx(0.5, abs=0.1)

    def test_a_silent_video_gains_the_track(self, tmp_path, ctx):
        silent = probe(make_video(tmp_path / "silent.mp4", seconds=1.0, audio=False))
        track = probe(make_audio(tmp_path / "track.wav", seconds=1.0, freq=1000))
        (result,) = run(replace_audio.spec(), silent, ctx, audio=track)
        assert result.has_audio is True

    def test_a_start_past_the_end_of_the_track_is_refused(self, clip, tmp_path, ctx):
        track = probe(make_audio(tmp_path / "track.wav", seconds=1.0))
        with pytest.raises(InvalidToolParams) as caught:
            run(replace_audio.spec(), clip, ctx, audio=track, start=1.5)
        assert caught.value.field == "start"

    def test_an_unattached_reference_is_refused(self, clip, ctx):
        with pytest.raises(UnsupportedMedia):
            run(replace_audio.spec(), clip, ctx, audio={"item_id": "abc"})

    def test_a_track_without_sound_is_refused(self, clip, tmp_path, ctx):
        mute_video = probe(make_video(tmp_path / "silent.mp4", seconds=1.0, audio=False))
        with pytest.raises(UnsupportedMedia):
            run(replace_audio.spec(), clip, ctx, audio=mute_video)

    def test_the_track_is_required(self):
        with pytest.raises(InvalidToolParams) as caught:
            validate_params(replace_audio.spec(), {})
        assert caught.value.field == "audio"

    def test_progress_reaches_the_end(self, clip, tmp_path):
        seen = []
        track = probe(make_audio(tmp_path / "track.wav", seconds=1.0))
        ctx = ToolContext(tmp_path / "scratch", progress=lambda fraction, label: seen.append(fraction))
        run(replace_audio.spec(), clip, ctx, audio=track)
        assert seen and seen[-1] == pytest.approx(1.0)
