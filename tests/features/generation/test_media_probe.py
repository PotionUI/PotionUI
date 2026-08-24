"""Tests for the shared ffprobe/cv2 video metadata probe.

Exercises the two entry points used by both the video generation output
handler and the media upload path: `get_video_dimensions` (pre-existing,
extracted here unchanged) and `get_video_duration_fps` (new).
"""

import json
import subprocess
from unittest.mock import Mock, patch

import pytest

from src.features.generation import media_probe


def _ffprobe_result(stdout: dict, returncode: int = 0):
    result = Mock()
    result.returncode = returncode
    result.stdout = json.dumps(stdout)
    return result


class TestGetVideoDimensions:
    def test_ffprobe_success(self):
        payload = {"streams": [{"width": 1920, "height": 1080}]}
        with patch("subprocess.run", return_value=_ffprobe_result(payload)):
            width, height = media_probe.get_video_dimensions("video.mp4")

        assert (width, height) == (1920, 1080)

    def test_ffprobe_missing_binary_falls_back_to_none_when_cv2_unavailable(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with patch.dict("sys.modules", {"cv2": None}):
                width, height = media_probe.get_video_dimensions("video.mp4")

        assert (width, height) == (None, None)

    def test_ffprobe_no_streams_falls_back(self):
        with patch("subprocess.run", return_value=_ffprobe_result({"streams": []})):
            with patch.dict("sys.modules", {"cv2": None}):
                width, height = media_probe.get_video_dimensions("video.mp4")

        assert (width, height) == (None, None)


class TestGetVideoDurationFps:
    def test_ffprobe_duration_from_format(self):
        payload = {
            "streams": [{"r_frame_rate": "24/1"}],
            "format": {"duration": "5.208333"},
        }
        with patch("subprocess.run", return_value=_ffprobe_result(payload)):
            duration, fps = media_probe.get_video_duration_fps("video.mp4")

        assert duration == 5.208333
        assert fps == 24.0

    def test_ffprobe_duration_prefers_stream_duration(self):
        payload = {
            "streams": [{"r_frame_rate": "30000/1001", "duration": "2.5"}],
            "format": {"duration": "9.9"},
        }
        with patch("subprocess.run", return_value=_ffprobe_result(payload)):
            duration, fps = media_probe.get_video_duration_fps("video.mp4")

        assert duration == 2.5
        assert round(fps, 3) == round(30000 / 1001, 3)

    def test_ffprobe_missing_binary_falls_back_to_none(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with patch.dict("sys.modules", {"cv2": None}):
                duration, fps = media_probe.get_video_duration_fps("video.mp4")

        assert (duration, fps) == (None, None)

    def test_malformed_frame_rate_is_ignored(self):
        payload = {"streams": [{"r_frame_rate": "0/0"}], "format": {"duration": "3.0"}}
        with patch("subprocess.run", return_value=_ffprobe_result(payload)):
            duration, fps = media_probe.get_video_duration_fps("video.mp4")

        assert duration == 3.0
        assert fps is None

    def test_timeout_falls_back_to_none(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)):
            with patch.dict("sys.modules", {"cv2": None}):
                duration, fps = media_probe.get_video_duration_fps("video.mp4")

        assert (duration, fps) == (None, None)


class TestGetAudioDurationSeconds:
    """The soundfile-backed probe behind audio upload metadata and the audio
    generation output handler's `duration_seconds` persistence."""

    def test_real_wav_file_reports_its_actual_duration(self, minimal_wav_file, wav_duration_seconds):
        """Drives a real file through a real `soundfile.info()` call - the
        point being that the probe actually decodes the container rather than
        guessing from its size."""
        duration = media_probe.get_audio_duration_seconds(str(minimal_wav_file))

        assert duration == pytest.approx(wav_duration_seconds)

    def test_missing_file_degrades_to_none(self):
        """Must not raise: a missing/corrupt upload must not fail the caller."""
        duration = media_probe.get_audio_duration_seconds("/nonexistent/audio.wav")

        assert duration is None

    def test_corrupt_file_degrades_to_none(self, tmp_path):
        bad_path = tmp_path / "not_really_audio.wav"
        bad_path.write_bytes(b"this is not a wav file at all")

        duration = media_probe.get_audio_duration_seconds(str(bad_path))

        assert duration is None

    def test_soundfile_unavailable_degrades_to_none(self, minimal_wav_file):
        with patch.dict("sys.modules", {"soundfile": None}):
            duration = media_probe.get_audio_duration_seconds(str(minimal_wav_file))

        assert duration is None
