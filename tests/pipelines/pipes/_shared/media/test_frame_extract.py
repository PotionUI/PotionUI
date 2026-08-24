"""Tests for extract_frame: frame-exact extraction via cv2, incl. out-of-range
and the sequential-read fallback path.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip(
    "cv2", reason="cv2 not available in this environment", exc_type=ImportError
)

from src.pipelines.pipes._shared.media.frame_extract import extract_frame  # noqa: E402


def _write_test_video(path: Path, colors: list[tuple[int, int, int]], size: int = 32, fps: float = 10.0) -> None:
    """Write a tiny mp4 with one solid-color frame per entry in `colors` (RGB)."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (size, size))
    assert writer.isOpened(), "cv2.VideoWriter failed to open -- codec unavailable in this environment"
    try:
        for r, g, b in colors:
            frame_bgr = np.zeros((size, size, 3), dtype=np.uint8)
            frame_bgr[:, :, 0] = b
            frame_bgr[:, :, 1] = g
            frame_bgr[:, :, 2] = r
            writer.write(frame_bgr)
    finally:
        writer.release()


# Distinct, saturated colors so a wrong-frame read is easy to catch.
_COLORS = [
    (255, 0, 0),    # frame 0: red
    (0, 255, 0),    # frame 1: green
    (0, 0, 255),    # frame 2: blue
    (255, 255, 0),  # frame 3: yellow
    (0, 255, 255),  # frame 4: cyan (last)
]


@pytest.fixture()
def sample_video(tmp_path) -> Path:
    path = tmp_path / "sample.mp4"
    _write_test_video(path, _COLORS)
    # cv2.VideoWriter can silently no-op on some minimal builds; verify the file
    # actually has readable frames before trusting it as a fixture.
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ret, _ = cap.read()
    cap.release()
    if not ret or total < len(_COLORS):
        pytest.skip("cv2 VideoWriter/VideoCapture round-trip not functional in this environment")
    return path


def _assert_color(img, rgb: tuple[int, int, int], tol: int = 40):
    arr = np.array(img.convert("RGB"))
    mean = arr.reshape(-1, 3).mean(axis=0)
    for channel, expected in zip(mean, rgb):
        assert abs(channel - expected) < tol, f"expected ~{rgb}, got mean {tuple(mean)}"


def test_extract_last_frame(sample_video):
    img = extract_frame(sample_video, -1)
    _assert_color(img, _COLORS[-1])


def test_extract_first_frame(sample_video):
    img = extract_frame(sample_video, 0)
    _assert_color(img, _COLORS[0])


def test_extract_middle_frame(sample_video):
    img = extract_frame(sample_video, 2)
    _assert_color(img, _COLORS[2])


def test_extract_negative_index_from_end(sample_video):
    img = extract_frame(sample_video, -2)
    _assert_color(img, _COLORS[-2])


def test_out_of_range_index_raises(sample_video):
    with pytest.raises(ValueError):
        extract_frame(sample_video, 999)


def test_negative_out_of_range_raises(sample_video):
    with pytest.raises(ValueError):
        extract_frame(sample_video, -999)


def test_unreadable_file_raises(tmp_path):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_bytes(b"not a real video file")
    with pytest.raises(ValueError):
        extract_frame(bogus, 0)


def test_sequential_read_fallback(monkeypatch, sample_video):
    # Force the seek-to-frame fast path to "fail" (return ret=False) so the
    # sequential-read fallback engages; the result must still be correct.
    # `cv2` is a singleton module object, so patching it here affects the
    # `import cv2` done inside `extract_frame`/`_sequential_read` too.
    real_video_capture = cv2.VideoCapture
    call_count = {"read": 0}

    class FlakySeekCapture:
        def __init__(self, path):
            self._cap = real_video_capture(path)

        def isOpened(self):
            return self._cap.isOpened()

        def get(self, prop):
            return self._cap.get(prop)

        def set(self, prop, value):
            return self._cap.set(prop, value)

        def read(self):
            call_count["read"] += 1
            # First read() call is the seek-fast-path attempt at frame index 2;
            # force it to report failure so _sequential_read is exercised.
            if call_count["read"] == 1:
                return False, None
            return self._cap.read()

        def release(self):
            return self._cap.release()

    monkeypatch.setattr(cv2, "VideoCapture", FlakySeekCapture)

    img = extract_frame(sample_video, 2)
    _assert_color(img, _COLORS[2])
