"""StreamingMp4Writer teardown contract, exercised without ffmpeg/cv2: the
subprocess handshake is what regressed in the field (communicate() flushes
stdin itself — pre-closing it raised "flush of closed file" on every
successful encode), so these tests stub Popen with real pipe processes."""

import subprocess

import numpy as np
import pytest

from src.pipelines.pipes.interpolator.rife import encode


@pytest.fixture
def ffmpeg_stub(monkeypatch):
    def _install(argv):
        monkeypatch.setattr(encode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
        real_popen = subprocess.Popen
        monkeypatch.setattr(
            encode.subprocess, "Popen", lambda cmd, **kw: real_popen(argv, **kw)
        )
    return _install


def test_close_after_successful_writes(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)

    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()

    assert writer._proc.returncode == 0


def test_nonzero_exit_raises_with_message(ffmpeg_stub):
    ffmpeg_stub(["false"])
    writer = encode.StreamingMp4Writer("/dev/null", 4, 4, 24.0)

    with pytest.raises(RuntimeError, match="exit 1"):
        writer.write(np.zeros((4, 4, 3), dtype=np.uint8))
        writer.close()


def test_odd_dimensions_rejected(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    with pytest.raises(ValueError, match="even dimensions"):
        encode.StreamingMp4Writer("/dev/null", 63, 64, 24.0)
