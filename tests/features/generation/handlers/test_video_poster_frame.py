"""Tests for `render_poster_frame` - the video-thumbnail decoder factored out
for preset media (see `src.features.media.store.get_preset_file`'s video
branch), independent of whether ffmpeg is actually installed in this
container. A real end-to-end decode is covered separately, skipped where
ffmpeg is absent - see `needs_ffmpeg` below.
"""

import shutil
import subprocess
from unittest.mock import Mock, patch

import pytest

from src.features.generation.handlers.video_handler import render_poster_frame

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


class TestRenderPosterFrame:
    def test_missing_binary_returns_none(self):
        """No ffmpeg on PATH must be a graceful None, not a raised exception -
        `MediaStore.get_preset_file` depends on this to fall back to serving
        the original video."""
        with patch("subprocess.run", side_effect=FileNotFoundError("no ffmpeg")):
            assert render_poster_frame("/tmp/does-not-matter.mp4", width=480) is None

    def test_timeout_returns_none(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10),
        ):
            assert render_poster_frame("/tmp/does-not-matter.mp4", width=480, timeout=10) is None

    def test_nonzero_return_code_returns_none(self):
        """A corrupt or unreadable source video: ffmpeg runs but fails."""
        failed = Mock(returncode=1, stderr=b"Invalid data found when processing input")
        with patch("subprocess.run", return_value=failed):
            assert render_poster_frame("/tmp/corrupt.mp4", width=480) is None

    def test_success_returns_the_written_bytes(self):
        """On a zero return code the temp file's bytes come back, not a path."""
        ok = Mock(returncode=0, stderr=b"")

        def fake_run(command, capture_output, timeout):
            # ffmpeg's output path is the command's last argument.
            with open(command[-1], "wb") as f:
                f.write(b"jpeg-bytes")
            return ok

        with patch("subprocess.run", side_effect=fake_run):
            result = render_poster_frame("/tmp/clip.mp4", width=480)

        assert result == b"jpeg-bytes"

    @needs_ffmpeg
    def test_real_decode_of_a_generated_clip(self, tmp_path):
        """End-to-end against the real ffmpeg binary, when present."""
        video_path = tmp_path / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                "-frames:v", "10", str(video_path),
            ],
            capture_output=True, check=True,
        )

        result = render_poster_frame(str(video_path), width=32)

        assert result is not None
        assert result[:2] == b"\xff\xd8"  # JPEG magic bytes
