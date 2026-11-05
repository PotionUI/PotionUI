from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import soundfile as sf
import torch
from PIL import Image

from src.pipelines.outputs import GenerationExecutionError
from src.pipelines.pipes.generator.video_minimax_h3.audio import AUDIO_SAMPLE_RATE
from src.pipelines.pipes.generator.video_minimax_h3.conditioning import ReferenceMedia, normalize_references
from src.pipelines.pipes.generator.video_minimax_h3.main import (
    GeneratorMinimaxH3Pipe,
    _decode_waveform_via_ffmpeg,
    _load_reference_audio,
)
from tests.pipelines.pipes.generator.video_minimax_h3.test_main import _fake_bundle, _fake_conditioning, _pipe_input
from tests.pipelines.pipes.generator.video_minimax_h3.test_silent_source import _MAIN, _forbid_decode, needs_ffmpeg
from tests.platform.media_tools.media_factory import run_ffmpeg

_TONE_HZ = 440


def _tone(rate: int, *, channels: int = 2) -> np.ndarray:
    times = np.arange(rate) / rate
    channel_tones = [0.5 / (index + 1) * np.sin(2 * np.pi * _TONE_HZ * times) for index in range(channels)]
    return np.stack(channel_tones, axis=1).astype(np.float32)


def _write_voice(path: Path, rate: int, *, channels: int = 2) -> Path:
    sf.write(str(path), _tone(rate, channels=channels), rate, subtype="FLOAT")
    return path


def _write_m4a(path: Path, rate: int) -> Path:
    run_ffmpeg("-f", "lavfi", "-i", f"sine=frequency={_TONE_HZ}:sample_rate={rate}:duration=1", "-c:a", "aac", str(path))
    return path


def _dominant_hz(waveform: torch.Tensor, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(waveform[0].numpy()))
    return float(np.argmax(spectrum)) * rate / waveform.shape[-1]


@needs_ffmpeg
@pytest.mark.parametrize("rate", [22050, 44100, 48000])
def test_a_voice_at_another_rate_reaches_the_audio_vae_rate_at_the_same_pitch(tmp_path, rate):
    media = _load_reference_audio(_write_voice(tmp_path / "voice.wav", rate))

    assert media.sample_rate == AUDIO_SAMPLE_RATE
    assert media.audio.shape[0] == 2
    assert media.audio.shape[1] == pytest.approx(AUDIO_SAMPLE_RATE, abs=AUDIO_SAMPLE_RATE // 100)
    assert _dominant_hz(media.audio, AUDIO_SAMPLE_RATE) == pytest.approx(_TONE_HZ, abs=1)

    normalized = normalize_references(
        [ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))), media],
        num_frames=12, reference_short_edge=32,
    )
    assert normalized[1].sample_rate == AUDIO_SAMPLE_RATE
    assert normalized[1].audio.shape == (2, AUDIO_SAMPLE_RATE // 2)


@needs_ffmpeg
def test_build_context_takes_a_48_khz_voice_beside_a_picture(tmp_path):
    voice = str(_write_voice(tmp_path / "voice.wav", 48000))
    pipe = GeneratorMinimaxH3Pipe({
        **GeneratorMinimaxH3Pipe.get_default_config(),
        "references": [{"path": "picture"}],
        "reference_audios": [{"path": voice}],
    })

    context = pipe.build_context(_pipe_input(
        model=_fake_bundle(), conditioning=[_fake_conditioning(3)],
        reference_images=[Image.new("RGB", (64, 48))], reference_audios=[voice],
    ))

    assert [reference.kind for reference in context.extra.references] == ["image", "audio"]
    assert context.extra.references[1].sample_rate == AUDIO_SAMPLE_RATE


@needs_ffmpeg
@pytest.mark.parametrize("rate", [44100, 32000])
def test_a_m4a_voice_reaches_the_audio_vae_rate_at_the_same_pitch(tmp_path, rate):
    media = _load_reference_audio(_write_m4a(tmp_path / "voice.m4a", rate))

    assert media.sample_rate == AUDIO_SAMPLE_RATE
    assert media.audio.shape[1] == pytest.approx(AUDIO_SAMPLE_RATE, abs=AUDIO_SAMPLE_RATE // 10)
    assert _dominant_hz(media.audio, AUDIO_SAMPLE_RATE) == pytest.approx(_TONE_HZ, abs=3)


def test_a_32_khz_voice_keeps_the_samples_of_its_source(tmp_path, monkeypatch):
    _forbid_decode(monkeypatch)

    media = _load_reference_audio(_write_voice(tmp_path / "voice.wav", AUDIO_SAMPLE_RATE))

    source = torch.from_numpy(_tone(AUDIO_SAMPLE_RATE).T.copy())
    assert media.sample_rate == AUDIO_SAMPLE_RATE
    assert torch.equal(media.audio, source)

    normalized = normalize_references(
        [ReferenceMedia(kind="image", image=Image.new("RGB", (32, 32))), media],
        num_frames=12, reference_short_edge=32,
    )
    assert torch.equal(normalized[1].audio, source[:, : AUDIO_SAMPLE_RATE // 2])


@pytest.mark.parametrize(
    "rate",
    [AUDIO_SAMPLE_RATE, pytest.param(48000, marks=needs_ffmpeg)],
)
def test_a_voice_of_more_than_two_channels_is_downmixed_at_any_rate(tmp_path, rate):
    media = _load_reference_audio(_write_voice(tmp_path / "voice.wav", rate, channels=6))

    assert media.audio.shape[0] == 1


@needs_ffmpeg
def test_a_file_neither_soundfile_nor_ffmpeg_can_read_raises_the_decode_error_naming_it(tmp_path):
    path = tmp_path / "not_a_voice.m4a"
    path.write_bytes(b"this is no audio at all")

    with pytest.raises(GenerationExecutionError, match="not_a_voice.m4a") as caught:
        _load_reference_audio(path)

    assert "could not decode" in str(caught.value)
    assert isinstance(caught.value.__context__, sf.LibsndfileError)


@pytest.mark.parametrize("error", [MemoryError("no room"), RuntimeError("not soundfile's refusal")])
def test_an_error_other_than_soundfile_refusing_the_file_is_not_retried_through_ffmpeg(monkeypatch, error):
    _forbid_decode(monkeypatch)

    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(sf, "read", fail)

    with pytest.raises(type(error), match=str(error)):
        _load_reference_audio("voice.wav")


def test_a_temp_wav_that_cannot_be_removed_does_not_replace_the_decode_error(tmp_path):
    failed_decode = SimpleNamespace(returncode=1, stderr=b"the real failure")
    with (
        patch(f"{_MAIN}._require_ffmpeg_or_raise", return_value="ffmpeg"),
        patch(f"{_MAIN}.tempfile.NamedTemporaryFile", return_value=SimpleNamespace(name=str(tmp_path / "out.wav"))),
        patch(f"{_MAIN}.subprocess.run", return_value=failed_decode),
        patch(f"{_MAIN}.Path") as path,
        patch(f"{_MAIN}.logger") as logger,
    ):
        path.return_value.unlink.side_effect = OSError("the file is held open")
        with pytest.raises(GenerationExecutionError, match="the real failure"):
            _decode_waveform_via_ffmpeg("voice.m4a")

    path.return_value.unlink.assert_called_once()
    logger.warning.assert_called_once()
    assert "the file is held open" in str(logger.warning.call_args)


def test_a_temp_wav_that_cannot_be_removed_does_not_fail_a_finished_decode(tmp_path):
    wav_path = tmp_path / "out.wav"

    def decode(_command, **_kwargs):
        sf.write(str(wav_path), _tone(AUDIO_SAMPLE_RATE), AUDIO_SAMPLE_RATE, subtype="FLOAT")
        return SimpleNamespace(returncode=0, stderr=b"")

    with (
        patch(f"{_MAIN}._require_ffmpeg_or_raise", return_value="ffmpeg"),
        patch(f"{_MAIN}.tempfile.NamedTemporaryFile", return_value=SimpleNamespace(name=str(wav_path))),
        patch(f"{_MAIN}.subprocess.run", side_effect=decode),
        patch(f"{_MAIN}.Path") as path,
        patch(f"{_MAIN}.logger") as logger,
    ):
        path.return_value.unlink.side_effect = OSError("the file is held open")
        waveform, rate = _decode_waveform_via_ffmpeg("voice.m4a")

    assert rate == AUDIO_SAMPLE_RATE
    assert waveform.shape == (2, AUDIO_SAMPLE_RATE)
    path.return_value.unlink.assert_called_once()
    logger.warning.assert_called_once()
