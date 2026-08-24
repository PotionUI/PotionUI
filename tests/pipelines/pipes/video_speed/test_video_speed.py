import os
import tempfile
from unittest.mock import patch, MagicMock, call

import pytest

from src.pipelines.contracts import PipeInput, PipeOutput, IOType
from src.pipelines.pipes.video_speed.main import VideoSpeedPipe


@pytest.fixture
def default_pipe():
    """Create a VideoSpeedPipe with default config."""
    return VideoSpeedPipe(VideoSpeedPipe.get_default_config())


@pytest.fixture
def generation_outputs():
    """Mock generation_outputs callable."""
    return MagicMock()


class TestPipeInitialization:
    def test_name(self, default_pipe):
        assert default_pipe.name == "video_speed"

    def test_description(self, default_pipe):
        assert "speed" in default_pipe.description.lower()

    def test_default_config(self):
        config = VideoSpeedPipe.get_default_config()
        assert config["speed_percent"] == 50
        assert config["keep_audio"] is True
        assert config["output_format"] == "mp4"


class TestSpecs:
    def test_inputs(self):
        inputs = VideoSpeedPipe.inputs()
        assert len(inputs) == 1
        assert inputs[0].name == "video"
        assert inputs[0].io_type == IOType.VIDEO
        assert inputs[0].is_array is True
        assert inputs[0].required is True

    def test_outputs(self):
        outputs = VideoSpeedPipe.outputs()
        assert len(outputs) == 1
        assert outputs[0].name == "video"
        assert outputs[0].io_type == IOType.VIDEO
        assert outputs[0].is_array is True

    def test_configuration(self):
        configs = VideoSpeedPipe.configuration()
        config_names = [c.name for c in configs]
        assert "speed_percent" in config_names
        assert "keep_audio" in config_names
        assert "output_format" in config_names

        speed_config = next(c for c in configs if c.name == "speed_percent")
        assert speed_config.min_value == 10
        assert speed_config.max_value == 100
        assert speed_config.param_type == int


class TestParseBool:
    def test_true_bool(self, default_pipe):
        assert default_pipe._parse_bool(True) is True

    def test_false_bool(self, default_pipe):
        assert default_pipe._parse_bool(False) is False

    def test_true_string(self, default_pipe):
        assert default_pipe._parse_bool("true") is True
        assert default_pipe._parse_bool("True") is True
        assert default_pipe._parse_bool("TRUE") is True

    def test_false_string(self, default_pipe):
        assert default_pipe._parse_bool("false") is False
        assert default_pipe._parse_bool("") is False

    def test_one_string(self, default_pipe):
        assert default_pipe._parse_bool("1") is True

    def test_yes_string(self, default_pipe):
        assert default_pipe._parse_bool("yes") is True


class TestBuildAtempoChain:
    def test_normal_range(self):
        """Speed factor >= 0.5 should produce a single atempo filter."""
        result = VideoSpeedPipe._build_atempo_chain(0.5)
        assert result == ["atempo=0.5"]

    def test_exactly_one(self):
        result = VideoSpeedPipe._build_atempo_chain(1.0)
        assert result == ["atempo=1.0"]

    def test_above_half(self):
        result = VideoSpeedPipe._build_atempo_chain(0.75)
        assert result == ["atempo=0.75"]

    def test_below_half(self):
        """Speed factor < 0.5 should chain multiple atempo=0.5 filters."""
        result = VideoSpeedPipe._build_atempo_chain(0.25)
        # 0.25 < 0.5, so first filter is atempo=0.5, remaining = 0.25/0.5 = 0.5
        assert result == ["atempo=0.5", "atempo=0.5"]

    def test_very_slow(self):
        """Very slow speed (0.1) should chain multiple atempo filters."""
        result = VideoSpeedPipe._build_atempo_chain(0.1)
        # 0.1 < 0.5 → atempo=0.5, remaining=0.2
        # 0.2 < 0.5 → atempo=0.5, remaining=0.4
        # 0.4 < 0.5 → atempo=0.5, remaining=0.8
        # 0.8 >= 0.5 → atempo=0.8
        assert len(result) == 4
        assert result[0] == "atempo=0.5"
        assert result[1] == "atempo=0.5"
        assert result[2] == "atempo=0.5"
        assert result[3].startswith("atempo=0.8")


class TestAudioProbingIntegration:
    """video_speed no longer owns ffprobe logic -- it calls the shared
    `_shared/media/video_encode` helpers. These tests exercise that wiring
    through the real `_change_speed` entry point rather than a private method.
    """

    def test_missing_ffprobe_binary_short_circuits(self, generation_outputs, monkeypatch):
        """The shared `has_audio_stream` guards on `shutil.which` before ever
        invoking `subprocess.run` -- unlike the old local implementation,
        which always shelled out and depended on catching `FileNotFoundError`.
        """
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": True, "output_format": "mp4"})

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: None
        )
        probe_run = MagicMock()
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", probe_run
        )
        encode_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        monkeypatch.setattr("src.pipelines.pipes.video_speed.main.subprocess.run", encode_run)
        monkeypatch.setattr("src.pipelines.pipes.video_speed.main.os.path.exists", lambda _p: True)
        monkeypatch.setattr("src.pipelines.pipes.video_speed.main.os.path.getsize", lambda _p: 1024)
        monkeypatch.setattr(
            "src.pipelines.pipes.video_speed.main.probe_source_duration", lambda _p: 5.0
        )

        result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        probe_run.assert_not_called()
        assert result is not None
        ffmpeg_cmd = encode_run.call_args[0][0]
        assert "-an" in ffmpeg_cmd
        assert "-filter:a" not in ffmpeg_cmd


class TestProcess:
    def test_empty_input(self, default_pipe, generation_outputs):
        result = default_pipe.process(
            PipeInput(input={"video": []}), generation_outputs
        )
        assert result.output == {"video": []}
        generation_outputs.assert_called()

    def test_no_video_key(self, default_pipe, generation_outputs):
        result = default_pipe.process(
            PipeInput(input={}), generation_outputs
        )
        assert result.output == {"video": []}

    @patch.object(VideoSpeedPipe, "_change_speed")
    def test_single_video_string(self, mock_change, default_pipe, generation_outputs):
        """A single string (not list) should be wrapped into a list."""
        mock_change.return_value = "/output/video.mp4"
        result = default_pipe.process(
            PipeInput(input={"video": "/input/video.mp4"}), generation_outputs
        )
        mock_change.assert_called_once()
        assert len(result.output["video"]) == 1

    @patch.object(VideoSpeedPipe, "_change_speed")
    def test_multiple_videos(self, mock_change, default_pipe, generation_outputs):
        mock_change.side_effect = ["/out/a.mp4", "/out/b.mp4"]
        result = default_pipe.process(
            PipeInput(input={"video": ["/in/a.mp4", "/in/b.mp4"]}),
            generation_outputs,
        )
        assert mock_change.call_count == 2
        assert len(result.output["video"]) == 2

    @patch.object(VideoSpeedPipe, "_change_speed")
    def test_failed_video(self, mock_change, default_pipe, generation_outputs):
        """If _change_speed returns None, video should not appear in output."""
        mock_change.return_value = None
        result = default_pipe.process(
            PipeInput(input={"video": ["/in/a.mp4"]}), generation_outputs
        )
        assert result.output["video"] == []


class TestChangeSpeed:
    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=True)
    @patch("src.pipelines.pipes.video_speed.main.probe_source_duration", return_value=10.0)
    def test_success_with_audio(self, mock_dur, mock_audio, mock_run, generation_outputs):
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": True, "output_format": "mp4"})

        # FFmpeg succeeds
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=1024 * 1024):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        assert result is not None
        assert result.endswith(".mp4")

        # Verify ffmpeg was called with atempo filter (not -an)
        ffmpeg_cmd = mock_run.call_args[0][0]
        assert "-filter:a" in ffmpeg_cmd
        assert "-an" not in ffmpeg_cmd

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=True)
    def test_success_no_audio_flag(self, mock_audio, mock_run, generation_outputs):
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": False, "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=1024), \
             patch("src.pipelines.pipes.video_speed.main.probe_source_duration", return_value=5.0):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        ffmpeg_cmd = mock_run.call_args[0][0]
        assert "-an" in ffmpeg_cmd
        assert "-filter:a" not in ffmpeg_cmd

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=False)
    def test_no_audio_stream(self, mock_audio, mock_run, generation_outputs):
        """When video has no audio stream, -an should be used regardless of keep_audio."""
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": True, "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=1024), \
             patch("src.pipelines.pipes.video_speed.main.probe_source_duration", return_value=5.0):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        ffmpeg_cmd = mock_run.call_args[0][0]
        assert "-an" in ffmpeg_cmd

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=False)
    def test_ffmpeg_failure(self, mock_audio, mock_run, generation_outputs):
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": False, "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Encoding error")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=False):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        assert result is None

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=False)
    def test_ffmpeg_timeout(self, mock_audio, mock_run, generation_outputs):
        from subprocess import TimeoutExpired
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": False, "output_format": "mp4"})
        mock_run.side_effect = TimeoutExpired(cmd="ffmpeg", timeout=600)

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=False):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        assert result is None

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=False)
    def test_setpts_calculation(self, mock_audio, mock_run, generation_outputs):
        """50% speed → setpts=2.0*PTS, 25% → setpts=4.0*PTS."""
        pipe = VideoSpeedPipe({"speed_percent": 25, "keep_audio": False, "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=1024), \
             patch("src.pipelines.pipes.video_speed.main.probe_source_duration", return_value=5.0):
            pipe._change_speed("/fake/input.mp4", generation_outputs)

        ffmpeg_cmd = mock_run.call_args[0][0]
        # setpts factor = 100/25 = 4.0
        assert "setpts=4.0*PTS" in ffmpeg_cmd

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=False)
    def test_empty_output_cleanup(self, mock_audio, mock_run, generation_outputs):
        """If ffmpeg produces an empty file, it should be cleaned up."""
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": False, "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=0), \
             patch("src.pipelines.pipes.video_speed.main.os.unlink") as mock_unlink:
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        assert result is None
        mock_unlink.assert_called_once()

    @patch("src.pipelines.pipes.video_speed.main.subprocess.run")
    @patch("src.pipelines.pipes.video_speed.main.has_audio_stream", return_value=True)
    def test_string_bool_conversion(self, mock_audio, mock_run, generation_outputs):
        """Config values from Jinja2 come as strings; keep_audio='true' should work."""
        pipe = VideoSpeedPipe({"speed_percent": 50, "keep_audio": "true", "output_format": "mp4"})
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("src.pipelines.pipes.video_speed.main.os.path.exists", return_value=True), \
             patch("src.pipelines.pipes.video_speed.main.os.path.getsize", return_value=1024), \
             patch("src.pipelines.pipes.video_speed.main.probe_source_duration", return_value=5.0):
            result = pipe._change_speed("/fake/input.mp4", generation_outputs)

        ffmpeg_cmd = mock_run.call_args[0][0]
        assert "-filter:a" in ffmpeg_cmd
        assert "-an" not in ffmpeg_cmd
