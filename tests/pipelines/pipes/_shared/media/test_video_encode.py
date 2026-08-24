"""Tests for encode_frames_to_mp4: format normalization, padding, real encode."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image

import wave as wave_module

from src.pipelines.pipes._shared.media.video_encode import (
    AudioTrack,
    FFmpegNotFoundError,
    _build_ffmpeg_args,
    _normalize_frames,
    _pad_to_even,
    _write_wav_pcm16,
    encode_frames_to_mp4,
    has_audio_stream,
    probe_decoded_frame_count,
    probe_effective_fps,
    probe_source_duration,
    probe_source_fps,
)

_MOD = "src.pipelines.pipes._shared.media.video_encode"

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_HAS_FFPROBE = shutil.which("ffprobe") is not None


def _gradient_frames(n: int = 8, size: int = 64) -> np.ndarray:
    """Synthetic (T,H,W,3) uint8 gradient frames, distinct per index."""
    frames = np.zeros((n, size, size, 3), dtype=np.uint8)
    for i in range(n):
        frames[i, :, :, 0] = i * (255 // max(n - 1, 1))
        frames[i, :, :, 1] = np.linspace(0, 255, size, dtype=np.uint8)[None, :]
        frames[i, :, :, 2] = np.linspace(0, 255, size, dtype=np.uint8)[:, None]
    return frames


class TestNormalizeFrames:
    def test_numpy_uint8_passthrough(self):
        frames = _gradient_frames(4, 16)
        out = _normalize_frames(frames)
        assert out.shape == (4, 16, 16, 3)
        assert out.dtype == np.uint8
        assert np.array_equal(out, frames)

    def test_numpy_wrong_shape_raises(self):
        with pytest.raises(ValueError):
            _normalize_frames(np.zeros((4, 16, 16), dtype=np.uint8))  # missing channel dim

    def test_pil_image_list(self):
        imgs = [Image.new("RGB", (10, 8), color=(i * 10, 0, 0)) for i in range(3)]
        out = _normalize_frames(imgs)
        assert out.shape == (3, 8, 10, 3)
        assert out.dtype == np.uint8
        assert out[1, 0, 0, 0] == 10

    def test_pil_image_list_converts_non_rgb(self):
        imgs = [Image.new("L", (10, 8), color=128)]
        out = _normalize_frames(imgs)
        assert out.shape == (1, 8, 10, 3)

    def test_empty_pil_list_raises(self):
        with pytest.raises(ValueError):
            _normalize_frames([])

    def test_float_tensor_tchw_in_0_1(self):
        t = torch.zeros(4, 3, 8, 10)
        t[:, 0, :, :] = 1.0  # full red
        out = _normalize_frames(t)
        assert out.shape == (4, 8, 10, 3)
        assert out.dtype == np.uint8
        assert (out[:, :, :, 0] == 255).all()
        assert (out[:, :, :, 1] == 0).all()

    def test_float_tensor_clamps_out_of_range(self):
        t = torch.full((1, 3, 4, 4), 2.0)  # way above 1.0
        out = _normalize_frames(t)
        assert (out == 255).all()

    def test_float_tensor_single_channel_expanded_to_rgb(self):
        t = torch.full((2, 1, 4, 4), 0.5)
        out = _normalize_frames(t)
        assert out.shape == (2, 4, 4, 3)
        assert (out[:, :, :, 0] == out[:, :, :, 1]).all()
        assert (out[:, :, :, 1] == out[:, :, :, 2]).all()

    def test_wrong_tensor_ndim_raises(self):
        with pytest.raises(ValueError):
            _normalize_frames(torch.zeros(3, 8, 10))  # missing batch/channel dim

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            _normalize_frames("not frames")


class TestPadToEven:
    def test_already_even_unchanged(self):
        frames = np.zeros((2, 8, 8, 3), dtype=np.uint8)
        out = _pad_to_even(frames)
        assert out.shape == frames.shape

    def test_odd_height_and_width_padded(self):
        frames = np.zeros((2, 7, 9, 3), dtype=np.uint8)
        out = _pad_to_even(frames)
        assert out.shape == (2, 8, 10, 3)

    def test_pad_uses_edge_values(self):
        frames = np.zeros((1, 3, 3, 3), dtype=np.uint8)
        frames[0, -1, :, 0] = 200  # last row marked
        out = _pad_to_even(frames)
        assert out.shape == (1, 4, 4, 3)
        # padded row should replicate the last real row (edge padding)
        assert (out[0, -1, :3, 0] == 200).all()


class TestBuildFfmpegArgs:
    """Pure argv construction -- no ffmpeg required."""

    def test_no_audio_matches_the_pre_audio_command(self):
        """Regression: byte-identical to the argv this helper replaced."""
        args = _build_ffmpeg_args(width=64, height=48, fps=24, codec="libx264", crf=18, out_path="/tmp/out.mp4")
        assert args == [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "64x48",
            "-r", "24",
            "-i", "-",
            "-an",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-movflags", "+faststart",
            "/tmp/out.mp4",
        ]

    def test_audio_path_adds_second_input_before_output_options(self):
        args = _build_ffmpeg_args(
            width=64, height=48, fps=24, codec="libx264", crf=18,
            out_path="/tmp/out.mp4", audio_path="/tmp/audio.wav",
        )
        assert args == [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "64x48",
            "-r", "24",
            "-i", "-",
            "-i", "/tmp/audio.wav",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-crf", "18",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "/tmp/out.mp4",
        ]

    def test_audio_path_maps_video_to_pipe_and_audio_to_second_input(self):
        """Regression for the wrong-stream-selection bug: without an explicit
        `-map`, ffmpeg picks the highest-resolution video stream across every
        input -- which could be `audio_path`'s own video track when it's an
        existing video file rather than a synthesized WAV."""
        args = _build_ffmpeg_args(
            width=64, height=48, fps=24, codec="libx264", crf=18,
            out_path="/tmp/out.mp4", audio_path="/tmp/source.mp4",
        )
        assert "-map" in args
        map_indices = [i for i, a in enumerate(args) if a == "-map"]
        mapped_targets = [args[i + 1] for i in map_indices]
        assert mapped_targets == ["0:v:0", "1:a:0"]
        # -map must come after both -i's and before any output/codec option.
        second_i_index = args.index("-i", args.index("-i") + 1)
        assert map_indices[0] > second_i_index
        assert map_indices[0] < args.index("-c:v")

    def test_audio_path_never_emits_an(self):
        args = _build_ffmpeg_args(
            width=8, height=8, fps=10, codec="libx264", crf=18,
            out_path="/tmp/out.mp4", audio_path="/tmp/audio.wav",
        )
        assert "-an" not in args

    def test_audio_path_accepts_pathlib_path(self, tmp_path):
        audio_file = tmp_path / "track.wav"
        args = _build_ffmpeg_args(
            width=8, height=8, fps=10, codec="libx264", crf=18,
            out_path=tmp_path / "out.mp4", audio_path=audio_file,
        )
        assert args[args.index("-i") + 1] == "-"  # video input still stdin
        # second -i is the audio path, stringified
        second_i_index = args.index("-i", args.index("-i") + 1)
        assert args[second_i_index + 1] == str(audio_file)
        assert args[-1] == str(tmp_path / "out.mp4")


class TestHasAudioStream:
    """Pure unit tests -- subprocess.run mocked, no real ffprobe needed."""

    def test_returns_false_when_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: None
        )
        assert has_audio_stream("/tmp/whatever.mp4") is False

    def test_returns_true_when_audio_stream_present(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: "/usr/bin/ffprobe"
        )

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": [{"codec_type": "audio"}]})

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", lambda *a, **kw: _Result()
        )
        assert has_audio_stream("/tmp/with_audio.mp4") is True

    def test_returns_false_when_no_audio_stream(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: "/usr/bin/ffprobe"
        )

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": []})

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", lambda *a, **kw: _Result()
        )
        assert has_audio_stream("/tmp/no_audio.mp4") is False

    def test_returns_false_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: "/usr/bin/ffprobe"
        )

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", lambda *a, **kw: _Result()
        )
        assert has_audio_stream("/tmp/broken.mp4") is False

    def test_returns_false_on_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: "/usr/bin/ffprobe"
        )

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", _raise
        )
        assert has_audio_stream("/tmp/slow.mp4") is False

    def test_returns_false_on_malformed_json(self, monkeypatch):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which", lambda _: "/usr/bin/ffprobe"
        )

        class _Result:
            returncode = 0
            stdout = "not json"

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", lambda *a, **kw: _Result()
        )
        assert has_audio_stream("/tmp/weird.mp4") is False


class TestProbeSourceFps:
    """Pure unit tests -- subprocess.run mocked, no real ffprobe needed."""

    def test_returns_none_when_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: None)
        assert probe_source_fps("/tmp/whatever.mp4") is None

    def test_parses_rational_r_frame_rate(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": [{"r_frame_rate": "24000/1001"}]})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_fps("/tmp/x.mp4") == pytest.approx(24000 / 1001)

    def test_returns_none_on_no_streams(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": []})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_fps("/tmp/x.mp4") is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_fps("/tmp/x.mp4") is None


class TestProbeSourceDuration:
    """Pure unit tests -- subprocess.run mocked, no real ffprobe needed."""

    def test_returns_none_when_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: None)
        assert probe_source_duration("/tmp/whatever.mp4") is None

    def test_parses_format_duration(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"format": {"duration": "12.5"}})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_duration("/tmp/x.mp4") == pytest.approx(12.5)

    def test_returns_none_when_duration_missing(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"format": {}})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_duration("/tmp/x.mp4") is None

    def test_returns_none_on_zero_duration(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"format": {"duration": "0"}})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_duration("/tmp/x.mp4") is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_duration("/tmp/x.mp4") is None

    def test_returns_none_on_timeout(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=10)

        monkeypatch.setattr(f"{_MOD}.subprocess.run", _raise)
        assert probe_source_duration("/tmp/slow.mp4") is None

    def test_returns_none_on_malformed_json(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = "not json"

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_source_duration("/tmp/weird.mp4") is None


class TestProbeDecodedFrameCount:
    """Pure unit tests -- subprocess.run mocked, no real ffprobe needed."""

    def test_returns_none_when_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: None)
        assert probe_decoded_frame_count("/tmp/whatever.mp4") is None

    def test_parses_nb_read_frames(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": [{"nb_read_frames": "240"}]})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_decoded_frame_count("/tmp/x.mp4") == 240

    def test_returns_none_when_field_missing(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": [{}]})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_decoded_frame_count("/tmp/x.mp4") is None

    def test_returns_none_on_no_streams(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 0
            stdout = json.dumps({"streams": []})

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_decoded_frame_count("/tmp/x.mp4") is None

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(f"{_MOD}.subprocess.run", lambda *a, **kw: _Result())
        assert probe_decoded_frame_count("/tmp/x.mp4") is None

    def test_returns_none_on_timeout(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.shutil.which", lambda _: "/usr/bin/ffprobe")

        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=60)

        monkeypatch.setattr(f"{_MOD}.subprocess.run", _raise)
        assert probe_decoded_frame_count("/tmp/slow.mp4") is None


class TestProbeEffectiveFps:
    """Combinator over probe_source_fps/probe_source_duration/
    probe_decoded_frame_count -- the three underlying probes are patched
    directly (each already covered in isolation above) so these tests focus
    purely on the preference logic."""

    def test_prefers_duration_derived_when_materially_disagrees_with_nominal(self, monkeypatch):
        # Nominal metadata says 30fps; the file actually decodes to 24fps
        # over its duration (e.g. a mislabeled/remuxed container).
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: 30.0)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: 5.0)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: 120)
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(24.0)

    def test_keeps_nominal_when_derived_roughly_agrees(self, monkeypatch):
        # 23.976 vs 240/10.0=24.0 -- well under the materiality threshold, so
        # the cleaner nominal rational is kept rather than the float estimate.
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: 23.976)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: 10.0)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: 240)
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(23.976)

    def test_falls_back_to_duration_derived_when_nominal_probe_fails(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: None)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: 5.0)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: 150)
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(30.0)

    def test_falls_back_to_nominal_when_duration_probe_fails(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: 25.0)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: None)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: 240)
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(25.0)

    def test_falls_back_to_nominal_when_frame_count_probe_fails(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: 25.0)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: 10.0)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: None)
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(25.0)

    def test_returns_none_when_every_probe_fails(self, monkeypatch):
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: None)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: None)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: None)
        assert probe_effective_fps("/tmp/x.mp4") is None

    def test_vfr_source_mislabeled_at_a_common_default(self, monkeypatch):
        # The repro shape: container metadata claims the pipe's own
        # 25.0 default, but the source actually plays back at 23.976 -- a
        # ~4% disagreement, comfortably over the materiality threshold.
        monkeypatch.setattr(f"{_MOD}.probe_source_fps", lambda _p: 25.0)
        monkeypatch.setattr(f"{_MOD}.probe_source_duration", lambda _p: 10.0)
        monkeypatch.setattr(f"{_MOD}.probe_decoded_frame_count", lambda _p: 240)  # 23.976 rounds up here
        assert probe_effective_fps("/tmp/x.mp4") == pytest.approx(24.0)


class TestEncodeAudioProbing:
    """`encode_frames_to_mp4`'s str/Path `audio=` branch probes before trusting
    the file has an audio stream (mocked -- no real ffmpeg/ffprobe needed)."""

    def test_str_audio_without_stream_degrades_to_no_audio(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which",
            lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
        )
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.has_audio_stream", lambda _p: False
        )

        captured_cmd = {}

        class _Result:
            returncode = 0
            stderr = b""

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"x")
            return _Result()

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", _fake_run
        )

        out_path = tmp_path / "out.mp4"
        encode_frames_to_mp4(_gradient_frames(2, 16), out_path, fps=24, audio="/tmp/no_audio_source.mp4")

        assert "-an" in captured_cmd["cmd"]
        assert "-map" not in captured_cmd["cmd"]

    def test_str_audio_with_stream_is_passed_through_with_maps(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.shutil.which",
            lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
        )
        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.has_audio_stream", lambda _p: True
        )

        captured_cmd = {}

        class _Result:
            returncode = 0
            stderr = b""

        def _fake_run(cmd, **kwargs):
            captured_cmd["cmd"] = cmd
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"x")
            return _Result()

        monkeypatch.setattr(
            "src.pipelines.pipes._shared.media.video_encode.subprocess.run", _fake_run
        )

        out_path = tmp_path / "out.mp4"
        encode_frames_to_mp4(_gradient_frames(2, 16), out_path, fps=24, audio="/tmp/with_audio_source.mp4")

        assert "/tmp/with_audio_source.mp4" in captured_cmd["cmd"]
        assert captured_cmd["cmd"].count("-map") == 2
        assert "-an" not in captured_cmd["cmd"]


class TestWriteWavPcm16:
    def test_mono_waveform_round_trips(self, tmp_path):
        out_path = tmp_path / "mono.wav"
        waveform = np.linspace(-1.0, 1.0, 1000, dtype=np.float32)[np.newaxis, :]  # (1, samples)

        _write_wav_pcm16(waveform, sample_rate=22050, out_path=out_path)

        with wave_module.open(str(out_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 22050
            assert wav_file.getnframes() == 1000

    def test_stereo_waveform_interleaves_channels(self, tmp_path):
        out_path = tmp_path / "stereo.wav"
        samples = 500
        left = np.full(samples, 0.5, dtype=np.float32)
        right = np.full(samples, -0.5, dtype=np.float32)
        waveform = np.stack([left, right], axis=0)  # (2, samples)

        _write_wav_pcm16(waveform, sample_rate=44100, out_path=out_path)

        with wave_module.open(str(out_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 2
            assert wav_file.getframerate() == 44100
            assert wav_file.getnframes() == samples

            raw = wav_file.readframes(samples)
            pcm = np.frombuffer(raw, dtype=np.int16).reshape(samples, 2)
            expected_left = int(round(0.5 * 32767.0))
            expected_right = int(round(-0.5 * 32767.0))
            assert (pcm[:, 0] == expected_left).all()
            assert (pcm[:, 1] == expected_right).all()

    def test_clips_out_of_range_values(self, tmp_path):
        out_path = tmp_path / "clipped.wav"
        waveform = np.array([[2.0, -2.0, 0.0]], dtype=np.float32)

        _write_wav_pcm16(waveform, sample_rate=8000, out_path=out_path)

        with wave_module.open(str(out_path), "rb") as wav_file:
            raw = wav_file.readframes(3)
            pcm = np.frombuffer(raw, dtype=np.int16)
            assert pcm[0] == 32767
            assert pcm[1] == -32767
            assert pcm[2] == 0

    def test_1d_waveform_treated_as_mono(self, tmp_path):
        out_path = tmp_path / "flat.wav"
        waveform = np.zeros(100, dtype=np.float32)

        _write_wav_pcm16(waveform, sample_rate=16000, out_path=out_path)

        with wave_module.open(str(out_path), "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getnframes() == 100

    def test_wrong_ndim_raises(self):
        with pytest.raises(ValueError):
            _write_wav_pcm16(np.zeros((2, 3, 4), dtype=np.float32), sample_rate=8000, out_path="/tmp/x.wav")


@pytest.mark.skipif(_HAS_FFMPEG, reason="only exercises the missing-binary path")
def test_encode_raises_clear_error_when_ffmpeg_missing():
    with pytest.raises(FFmpegNotFoundError, match="ffmpeg"):
        encode_frames_to_mp4(_gradient_frames(2, 8), "/tmp/should-not-be-created.mp4", fps=24)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed")
class TestRealEncode:
    def test_encode_gradient_frames_produces_file(self, tmp_path):
        out_path = tmp_path / "out.mp4"
        result = encode_frames_to_mp4(_gradient_frames(8, 64), out_path, fps=24)

        assert result == out_path
        assert out_path.exists()
        assert out_path.stat().st_size > 0

    def test_encode_odd_dimensions_succeeds(self, tmp_path):
        out_path = tmp_path / "odd.mp4"
        frames = np.zeros((4, 63, 65, 3), dtype=np.uint8)  # odd H and W
        result = encode_frames_to_mp4(frames, out_path, fps=12)
        assert result.exists()

    def test_encode_from_pil_list(self, tmp_path):
        out_path = tmp_path / "pil.mp4"
        imgs = [Image.new("RGB", (32, 32), color=(i * 30, 0, 0)) for i in range(6)]
        result = encode_frames_to_mp4(imgs, out_path, fps=10)
        assert result.exists()

    def test_encode_from_float_tensor(self, tmp_path):
        out_path = tmp_path / "tensor.mp4"
        t = torch.rand(6, 3, 32, 32)
        result = encode_frames_to_mp4(t, out_path, fps=10)
        assert result.exists()

    @pytest.mark.skipif(not _HAS_FFPROBE, reason="ffprobe not installed")
    def test_encode_frame_count_and_fps_via_ffprobe(self, tmp_path):
        out_path = tmp_path / "probe.mp4"
        n_frames, fps = 8, 24
        encode_frames_to_mp4(_gradient_frames(n_frames, 64), out_path, fps=fps)

        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "v:0", str(out_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(probe.stdout)
        stream = data["streams"][0]

        assert int(stream["nb_frames"]) == n_frames
        num, den = (int(x) for x in stream["r_frame_rate"].split("/"))
        assert round(num / den) == fps

    def test_encode_empty_out_dir_is_created(self, tmp_path):
        out_path = tmp_path / "nested" / "dir" / "out.mp4"
        result = encode_frames_to_mp4(_gradient_frames(2, 16), out_path, fps=24)
        assert result.exists()

    @pytest.mark.skipif(not _HAS_FFPROBE, reason="ffprobe not installed")
    def test_encode_with_audio_track_produces_audio_stream(self, tmp_path):
        out_path = tmp_path / "with_audio.mp4"
        fps, n_frames = 24, 10
        sample_rate = 44100
        duration_s = 0.5
        t = np.linspace(0, duration_s, int(sample_rate * duration_s), dtype=np.float32)
        sine = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)[np.newaxis, :]  # mono

        result = encode_frames_to_mp4(
            _gradient_frames(n_frames, 64), out_path, fps=fps,
            audio=AudioTrack(waveform=sine, sample_rate=sample_rate),
        )

        assert result.exists()
        assert result.stat().st_size > 0

        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "a:0", str(out_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(probe.stdout)
        assert len(data["streams"]) == 1
        assert data["streams"][0]["codec_type"] == "audio"

    @pytest.mark.skipif(not _HAS_FFPROBE, reason="ffprobe not installed")
    def test_audio_str_path_without_audio_stream_falls_back_silently(self, tmp_path):
        """A source video with no audio track (e.g. an upscale mode's source
        clip that never had sound) must encode exactly like `audio=None` --
        no ffmpeg failure, no audio stream in the result."""
        source = tmp_path / "source_no_audio.mp4"
        gen = subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=32x32:rate=10:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
            capture_output=True, timeout=30,
        )
        assert gen.returncode == 0 and source.exists()
        assert has_audio_stream(source) is False

        out_path = tmp_path / "out.mp4"
        result = encode_frames_to_mp4(_gradient_frames(6, 32), out_path, fps=10, audio=str(source))
        assert result.exists()

        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "a", str(out_path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(probe.stdout)
        assert data.get("streams", []) == []

    @pytest.mark.skipif(not _HAS_FFPROBE, reason="ffprobe not installed")
    def test_audio_str_path_keeps_piped_video_over_higher_res_source(self, tmp_path):
        """Regression for the wrong-stream-selection bug: the source's own
        video stream is a HIGHER resolution than the piped frames, so
        ffmpeg's automatic (map-less) stream selection would prefer it --
        the explicit `-map` must keep the piped (correct) video instead."""
        source = tmp_path / "source_with_audio.mp4"
        gen = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=size=128x128:rate=10:duration=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(source),
            ],
            capture_output=True, timeout=30,
        )
        assert gen.returncode == 0 and source.exists()
        assert has_audio_stream(source) is True

        out_path = tmp_path / "out.mp4"
        result = encode_frames_to_mp4(_gradient_frames(6, 32), out_path, fps=10, audio=str(source))
        assert result.exists()

        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(out_path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(probe.stdout)
        v_streams = [s for s in data["streams"] if s["codec_type"] == "video"]
        a_streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
        assert len(v_streams) == 1
        # 32x32 -- the piped frames' resolution, NOT the source's 128x128.
        assert v_streams[0]["width"] == 32
        assert v_streams[0]["height"] == 32
        assert len(a_streams) == 1
