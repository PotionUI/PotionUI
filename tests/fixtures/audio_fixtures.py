"""Audio fixtures - a real, minimal WAV file built with the stdlib `wave` module.

Every test that exercises the save / duration-probe path needs a genuine
playable file, not a stub: real PCM bytes behind a real WAV header that
`soundfile` (the library the duration probe uses) can open and report an
exact duration for - a test asserting on a probed duration needs the probe to
actually run against something real, or it proves nothing about either.
"""

import io
import wave
from typing import Tuple

import pytest

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
DURATION_SECONDS = 0.5
FRAME_COUNT = int(round(DURATION_SECONDS * SAMPLE_RATE))


def build_minimal_wav(
    duration_seconds: float = DURATION_SECONDS,
    sample_rate: int = SAMPLE_RATE,
    channels: int = CHANNELS,
) -> bytes:
    """A complete, valid PCM `.wav` of exactly `duration_seconds` at
    `sample_rate`/`channels` - silence, but a real decodable file.

    Returns:
        The full file as bytes.
    """
    frame_count = int(round(duration_seconds * sample_rate))
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(SAMPLE_WIDTH)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b'\x00' * SAMPLE_WIDTH * frame_count * channels)
    return buf.getvalue()


@pytest.fixture
def minimal_wav_bytes() -> bytes:
    """A valid half-second mono PCM `.wav` as bytes."""
    return build_minimal_wav()


@pytest.fixture
def minimal_wav_file(tmp_path, minimal_wav_bytes):
    """A valid half-second mono PCM `.wav` written to a temporary path."""
    path = tmp_path / "source_audio.wav"
    path.write_bytes(minimal_wav_bytes)
    return path


@pytest.fixture
def wav_duration_seconds() -> float:
    """The exact duration `minimal_wav_file`/`minimal_wav_bytes` decode to."""
    return DURATION_SECONDS
