from pathlib import Path
from types import SimpleNamespace

import pytest

from src.platform.util import video_transcode as vt


def _probe_payload(codec="vp9", audio=False):
    streams = [{"codec_type": "video", "codec_name": codec, "width": 1920, "height": 1080,
                "avg_frame_rate": "30000/1001", "duration": "5.5"}]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {"streams": streams, "format": {"duration": "5.6"}}


class TestParseProbe:
    def test_reads_video_stream(self):
        probe = vt.parse_probe(_probe_payload(audio=True), 1234)
        assert probe.width == 1920 and probe.height == 1080
        assert probe.duration == 5.5
        assert probe.has_audio is True
        assert probe.video_codec == "vp9"
        assert probe.size_bytes == 1234
        assert round(probe.fps, 2) == 29.97

    def test_no_video_stream_is_none(self):
        assert vt.parse_probe({"streams": [{"codec_type": "audio"}]}, 1) is None

    def test_duration_falls_back_to_format(self):
        payload = _probe_payload()
        del payload["streams"][0]["duration"]
        assert vt.parse_probe(payload, 1).duration == 5.6


class TestCommand:
    def test_webm_command(self, tmp_path):
        cmd = vt.build_transcode_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "o.tmp",
                                         vt.TranscodeSpec(container="webm", start=1.5, length=4, fps=24))
        assert cmd[cmd.index("-ss") + 1] == "1.500"
        assert cmd[cmd.index("-t") + 1] == "4.000"
        assert cmd[cmd.index("-c:v") + 1] == "libvpx-vp9"
        assert cmd[cmd.index("-crf") + 1] == "34"
        assert "-an" in cmd
        assert cmd[cmd.index("-f") + 1] == "webm"
        assert "fps=24" in cmd[cmd.index("-vf") + 1]
        assert cmd[cmd.index("-fps_mode") + 1] == "cfr"

    def test_mp4_command_has_faststart_and_even_scale(self, tmp_path):
        cmd = vt.build_transcode_command("ffmpeg", tmp_path / "a.webm", tmp_path / "o.tmp",
                                         vt.TranscodeSpec(container="mp4"))
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "26"
        assert "+faststart" in cmd
        assert ":-2" in cmd[cmd.index("-vf") + 1]
        assert "-ss" not in cmd and "-t" not in cmd

    def test_keep_audio_maps_audio(self, tmp_path):
        cmd = vt.build_transcode_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "o.tmp",
                                         vt.TranscodeSpec(container="webm", keep_audio=True))
        assert "-an" not in cmd
        assert cmd[cmd.index("-c:a") + 1] == "libopus"

    def test_width_scale_reduces_even_width(self):
        assert vt.scale_filter(1280, 0.65) == "scale='min(832,iw)':-2:flags=lanczos"

    def test_unknown_container_rejected(self, tmp_path):
        with pytest.raises(vt.VideoTranscodeError):
            vt.build_transcode_command("ffmpeg", tmp_path / "a", tmp_path / "b", vt.TranscodeSpec(container="avi"))

    def test_target_fps_caps_source(self):
        assert vt.target_fps(60.0) == 30.0
        assert vt.target_fps(0.0) == 24.0
        assert vt.target_fps(25.0, 12) == 12.0


class TestTranscode:
    def test_missing_ffmpeg_raises_before_writing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vt, "find_ffmpeg", lambda: None)
        dest = tmp_path / "o.webm"
        with pytest.raises(vt.FfmpegUnavailableError):
            vt.transcode_video(tmp_path / "a.mp4", dest, vt.TranscodeSpec(container="webm"))
        assert not dest.exists()

    def _fake_run(self, sizes, calls):
        def run(command, **_kwargs):
            calls.append(command)
            Path(command[-1]).write_bytes(b"x" * sizes[min(len(calls), len(sizes)) - 1])
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        return run

    def test_steps_down_ladder_until_it_fits(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(vt, "find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(vt, "probe_video", lambda path: None)
        monkeypatch.setattr(vt.subprocess, "run", self._fake_run([5000, 3000, 900], calls))
        dest = tmp_path / "o.webm"
        result = vt.transcode_video(tmp_path / "a.mp4", dest, vt.TranscodeSpec(container="webm"), max_bytes=1000)
        assert result.bytes == 900
        assert len(calls) == 3
        assert result.crf == 42
        assert result.width_scale == 0.8

    def test_gives_up_and_removes_file(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(vt, "find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(vt, "probe_video", lambda path: None)
        monkeypatch.setattr(vt.subprocess, "run", self._fake_run([5000], calls))
        dest = tmp_path / "o.webm"
        with pytest.raises(vt.VideoTranscodeError):
            vt.transcode_video(tmp_path / "a.mp4", dest, vt.TranscodeSpec(container="webm"), max_bytes=10)
        assert not dest.exists()
        assert len(calls) == len(vt.quality_ladder("webm"))

    def test_single_pass_without_budget(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(vt, "find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(vt, "probe_video", lambda path: None)
        monkeypatch.setattr(vt.subprocess, "run", self._fake_run([5000], calls))
        vt.transcode_video(tmp_path / "a.mp4", tmp_path / "o.mp4", vt.TranscodeSpec(container="mp4"))
        assert len(calls) == 1

    def test_ffmpeg_failure_leaves_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vt, "find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(vt, "probe_video", lambda path: None)
        dest = tmp_path / "o.webm"

        def run(command, **_kwargs):
            Path(command[-1]).write_bytes(b"partial")
            return SimpleNamespace(returncode=1, stderr="boom", stdout="")

        monkeypatch.setattr(vt.subprocess, "run", run)
        with pytest.raises(vt.VideoTranscodeError):
            vt.transcode_video(tmp_path / "a.mp4", dest, vt.TranscodeSpec(container="webm"))
        assert not dest.exists()

    def test_probe_without_ffprobe_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vt, "find_ffprobe", lambda: None)
        assert vt.probe_video(tmp_path / "a.mp4") is None
