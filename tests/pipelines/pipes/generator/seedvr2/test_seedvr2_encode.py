"""Tests for `generator/seedvr2/encode.py`'s video/audio failure split.

Every scenario is exercised with fake `encode_video`/`mux_audio`/`has_audio`
callables -- no ffmpeg, no filesystem writes.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.generator.seedvr2.encode import (
    AUDIO_MUXED,
    AUDIO_MUX_FAILED,
    AUDIO_NOT_REQUESTED,
    AUDIO_SILENT_SOURCE,
    encode_video_with_audio,
)

_FRAMES = np.zeros((2, 4, 4, 3), dtype=np.uint8)


class _Recorder:
    """Records calls made to a fake callable and returns/raises as configured."""

    def __init__(self, *, raises: Exception | None = None, returns=None):
        self.calls: list[tuple] = []
        self._raises = raises
        self._returns = returns

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._returns


def test_video_encoder_failure_propagates_and_never_attempts_mux():
    encode_video = _Recorder(raises=RuntimeError("ffmpeg failed (exit 1): bad rawvideo geometry"))
    mux_audio = _Recorder()

    with pytest.raises(RuntimeError, match="bad rawvideo geometry"):
        encode_video_with_audio(
            _FRAMES, "/tmp/out.mp4", 24.0,
            source_audio_path="/tmp/src.mp4", keep_audio=True,
            encode_video=encode_video, mux_audio=mux_audio, has_audio=lambda p: True,
        )

    assert len(encode_video.calls) == 1
    assert mux_audio.calls == []  # never reached -- video failures are never treated as audio problems


def test_video_encoder_failure_propagates_even_when_audio_not_requested():
    encode_video = _Recorder(raises=RuntimeError("ffmpeg timed out after 600s"))

    with pytest.raises(RuntimeError, match="timed out"):
        encode_video_with_audio(
            _FRAMES, "/tmp/out.mp4", 24.0,
            source_audio_path=None, keep_audio=False,
            encode_video=encode_video, mux_audio=_Recorder(),
        )


def test_keep_audio_false_skips_mux_entirely():
    encode_video = _Recorder()
    mux_audio = _Recorder()
    has_audio = _Recorder(returns=True)

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=False,
        encode_video=encode_video, mux_audio=mux_audio, has_audio=has_audio,
    )

    assert result.audio_outcome == AUDIO_NOT_REQUESTED
    assert result.video_path == "/tmp/out.mp4"
    assert mux_audio.calls == []
    assert has_audio.calls == []  # not even probed -- audio was never wanted


def test_silent_source_is_not_treated_as_a_failure():
    encode_video = _Recorder()
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio, has_audio=lambda p: False,
    )

    assert result.audio_outcome == AUDIO_SILENT_SOURCE
    assert result.omitted_reason is None
    assert result.video_path == "/tmp/out.mp4"
    assert mux_audio.calls == []  # no audio stream to mux -- never invoked


def test_genuine_mux_failure_falls_back_to_video_only_with_reason():
    encode_video = _Recorder()
    mux_audio = _Recorder(raises=RuntimeError("ffmpeg audio mux failed (exit 1): bad audio codec"))

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio, has_audio=lambda p: True,
    )

    assert result.audio_outcome == AUDIO_MUX_FAILED
    assert "bad audio codec" in result.omitted_reason
    assert result.video_path == "/tmp/out.mp4"  # the already-successful silent video, not lost
    assert len(mux_audio.calls) == 1


def test_successful_passthrough_reports_audio_muxed():
    encode_video = _Recorder()
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio, has_audio=lambda p: True,
    )

    assert result.audio_outcome == AUDIO_MUXED
    assert result.video_path == "/tmp/out.mp4.audio.mp4"
    assert mux_audio.calls == [(("/tmp/out.mp4", "/tmp/src.mp4", "/tmp/out.mp4.audio.mp4"), {})]
