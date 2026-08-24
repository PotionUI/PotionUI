"""Tests for chain_video_wan22.stitch: overlap-dropping frame concatenation,
with an injectable frame reader/encoder so this runs without cv2/ffmpeg, plus
one real-cv2-backed round trip guarded by importorskip.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from src.pipelines.pipes.generator.chain_video_wan22 import stitch as stitch_mod
from src.pipelines.pipes.generator.chain_video_wan22.stitch import stitch_segments


def _reader_factory(segments_frames):
    """segments_frames: {path: [frame_value, frame_value, ...]} -> a fake
    frame_reader yielding tiny (1,1,3) uint8 frames filled with each value."""
    def reader(path):
        for value in segments_frames[path]:
            yield np.full((1, 1, 3), value, dtype=np.uint8)
    return reader


def test_crossfades_the_overlap_of_non_first_segments():
    # The overlap region dissolves the previous tail into the current head; the
    # total frame count is unchanged from the old hard-drop (Lp + Lc - overlap).
    segments_frames = {
        "seg0.mp4": [0, 0, 0, 0, 0],       # 5 frames, all kept (first segment)
        "seg1.mp4": [120, 120, 120, 120],  # first 2 crossfade with seg0's tail [0, 0]
    }
    reader = _reader_factory(segments_frames)
    captured = {}

    def fake_encode(frames, out_path, fps):
        captured["frames"] = frames
        captured["out_path"] = out_path
        captured["fps"] = fps

    stitch_segments(list(segments_frames), overlap=2, out_path="out.mp4", fps=24,
                     frame_reader=reader, encode=fake_encode)

    values = captured["frames"][:, 0, 0, 0].tolist()
    # overlap frames: alpha = 1/3, 2/3 -> round(0*2/3 + 120*1/3)=40, round(0*1/3 + 120*2/3)=80.
    assert values == [0, 0, 0, 40, 80, 120, 120]
    assert captured["frames"].shape == (7, 1, 1, 3)  # 5 + 4 - 2 (same length as a hard drop)
    assert captured["out_path"] == "out.mp4"
    assert captured["fps"] == 24


def test_crossfade_endpoint_weights_are_linear():
    # A constant tail (0) blended with a constant head (100) across overlap=3
    # gives evenly spaced steps: alpha = 1/4, 2/4, 3/4 -> 25, 50, 75.
    segments_frames = {"a.mp4": [0, 0, 0], "b.mp4": [100, 100, 100, 100]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=3, out_path="o.mp4", fps=24,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))
    assert captured["frames"][:, 0, 0, 0].tolist() == [25, 50, 75, 100]


def test_zero_overlap_keeps_every_frame():
    segments_frames = {"a.mp4": [1, 2], "b.mp4": [3, 4]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=0, out_path="o.mp4", fps=24,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))
    assert captured["frames"][:, 0, 0, 0].tolist() == [1, 2, 3, 4]


def test_single_segment_no_dropping():
    segments_frames = {"only.mp4": [5, 6, 7]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=4, out_path="o.mp4", fps=24,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))
    # Only segment 0 -> never trimmed, even though overlap (4) exceeds its length.
    assert captured["frames"][:, 0, 0, 0].tolist() == [5, 6, 7]


def test_overlap_larger_than_non_first_segment_raises():
    segments_frames = {"a.mp4": [1, 2, 3], "b.mp4": [4, 5]}  # only 2 frames, overlap=3
    reader = _reader_factory(segments_frames)
    with pytest.raises(ValueError, match="not enough"):
        stitch_segments(list(segments_frames), overlap=3, out_path="o.mp4", fps=24,
                         frame_reader=reader, encode=lambda f, p, fps: None)


def test_empty_segment_paths_raises():
    with pytest.raises(ValueError, match="at least one"):
        stitch_segments([], overlap=2, out_path="o.mp4", fps=24,
                         frame_reader=lambda p: iter([]), encode=lambda f, p, fps: None)


def test_sequential_reader_never_holds_two_segments_open():
    """The reader is invoked once per segment, in order -- never re-entered for
    a later segment before the earlier one's generator is exhausted."""
    order = []

    def reader(path):
        order.append(("open", path))
        for v in {"a.mp4": [1, 2], "b.mp4": [3, 4], "c.mp4": [5, 6]}[path]:
            yield np.full((1, 1, 3), v, dtype=np.uint8)
        order.append(("close", path))

    stitch_segments(["a.mp4", "b.mp4", "c.mp4"], overlap=0, out_path="o.mp4", fps=24,
                     frame_reader=reader, encode=lambda f, p, fps: None)

    assert order == [
        ("open", "a.mp4"), ("close", "a.mp4"),
        ("open", "b.mp4"), ("close", "b.mp4"),
        ("open", "c.mp4"), ("close", "c.mp4"),
    ]


# -- zero-overlap ffmpeg stream-copy fast path (mocked subprocess) ----------
#
# `probe_source_fps` (used by `_can_stream_copy`) calls `subprocess.run`
# through `video_encode`'s own module namespace, and `_stream_copy_concat`
# calls it through `stitch`'s -- but both names are bound to the one real
# `subprocess` module object, so a single monkeypatch of `subprocess.run`
# (dispatching on argv[0]) intercepts both call sites.

def _fake_ffprobe_result(fps: str):
    class _Result:
        returncode = 0
        stdout = json.dumps({"streams": [{"r_frame_rate": fps}]})
    return _Result()


def _dispatching_run(ffprobe_fps: str, ffmpeg_side_effect):
    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return _fake_ffprobe_result(ffprobe_fps)
        return ffmpeg_side_effect(cmd, **kwargs)
    return run


def test_zero_overlap_uses_stream_copy_when_ffmpeg_available_and_fps_match(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def ffmpeg_side_effect(cmd, **kwargs):
        # -c copy concat: write bytes to the requested output path, as real
        # ffmpeg would, so `_stream_copy_concat`'s existence/size check passes.
        Path(cmd[-1]).write_bytes(b"stitched")
        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(stitch_mod.subprocess, "run", _dispatching_run("24/1", ffmpeg_side_effect))

    seg0, seg1 = tmp_path / "seg0.mp4", tmp_path / "seg1.mp4"
    seg0.write_bytes(b"a")
    seg1.write_bytes(b"b")
    out_path = tmp_path / "out.mp4"

    def fail_reader(path):
        raise AssertionError("frame reader must not run on the stream-copy path")

    def fail_encode(*a, **kw):
        raise AssertionError("encode must not run on the stream-copy path")

    result = stitch_segments([seg0, seg1], overlap=0, out_path=out_path, fps=24.0,
                              frame_reader=fail_reader, encode=fail_encode)

    assert result == out_path
    assert out_path.read_bytes() == b"stitched"


def test_stream_copy_command_is_concat_demuxer_with_stream_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    captured = {}

    def ffmpeg_side_effect(cmd, **kwargs):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"x")
        class _Result:
            returncode = 0
        return _Result()

    monkeypatch.setattr(stitch_mod.subprocess, "run", _dispatching_run("30/1", ffmpeg_side_effect))

    seg0, seg1 = tmp_path / "seg0.mp4", tmp_path / "seg1.mp4"
    seg0.write_bytes(b"a")
    seg1.write_bytes(b"b")
    out_path = tmp_path / "out.mp4"

    stitch_segments([seg0, seg1], overlap=0, out_path=out_path, fps=30.0,
                     frame_reader=lambda p: iter([]), encode=lambda f, p, fps: None)

    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "concat"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert cmd[-1] == str(out_path)


def test_ffmpeg_missing_falls_back_to_frame_accurate_path(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: None)

    def run_should_not_be_called(cmd, **kwargs):
        raise AssertionError("subprocess.run must not be called when ffmpeg is missing")

    monkeypatch.setattr(stitch_mod.subprocess, "run", run_should_not_be_called)

    segments_frames = {"a.mp4": [1, 2], "b.mp4": [3, 4]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=0, out_path=tmp_path / "o.mp4", fps=24.0,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))

    assert captured["frames"][:, 0, 0, 0].tolist() == [1, 2, 3, 4]


def test_mismatched_fps_segment_falls_back_to_frame_accurate_path(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def ffmpeg_should_not_be_called(cmd, **kwargs):
        raise AssertionError("ffmpeg concat must not run when a segment's fps doesn't match")

    # First segment probes at the target fps, second probes at a different one.
    probe_calls = {"n": 0}

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            probe_calls["n"] += 1
            fps = "24/1" if probe_calls["n"] == 1 else "30/1"
            return _fake_ffprobe_result(fps)
        return ffmpeg_should_not_be_called(cmd, **kwargs)

    monkeypatch.setattr(stitch_mod.subprocess, "run", run)

    segments_frames = {"a.mp4": [1, 2], "b.mp4": [3, 4]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=0, out_path=tmp_path / "o.mp4", fps=24.0,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))

    assert captured["frames"][:, 0, 0, 0].tolist() == [1, 2, 3, 4]


def test_unprobeable_segment_falls_back_to_frame_accurate_path(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            class _Result:
                returncode = 1
                stdout = ""
            return _Result()
        raise AssertionError("ffmpeg concat must not run when a segment's fps can't be probed")

    monkeypatch.setattr(stitch_mod.subprocess, "run", run)

    segments_frames = {"a.mp4": [1, 2], "b.mp4": [3, 4]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=0, out_path=tmp_path / "o.mp4", fps=24.0,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))

    assert captured["frames"][:, 0, 0, 0].tolist() == [1, 2, 3, 4]


def test_ffmpeg_concat_failure_falls_back_to_frame_accurate_path(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def ffmpeg_side_effect(cmd, **kwargs):
        class _Result:
            returncode = 1
        return _Result()

    monkeypatch.setattr(stitch_mod.subprocess, "run", _dispatching_run("24/1", ffmpeg_side_effect))

    segments_frames = {"a.mp4": [1, 2], "b.mp4": [3, 4]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=0, out_path=tmp_path / "o.mp4", fps=24.0,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))

    assert captured["frames"][:, 0, 0, 0].tolist() == [1, 2, 3, 4]


def test_overlap_nonzero_never_attempts_stream_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(stitch_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def run_should_not_be_called(cmd, **kwargs):
        raise AssertionError("stream-copy path must not be attempted when overlap > 0")

    monkeypatch.setattr(stitch_mod.subprocess, "run", run_should_not_be_called)

    segments_frames = {"a.mp4": [0, 0, 0], "b.mp4": [100, 100, 100, 100]}
    reader = _reader_factory(segments_frames)
    captured = {}
    stitch_segments(list(segments_frames), overlap=3, out_path=tmp_path / "o.mp4", fps=24.0,
                     frame_reader=reader, encode=lambda f, p, fps: captured.update(frames=f))

    assert captured["frames"][:, 0, 0, 0].tolist() == [25, 50, 75, 100]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not available in this environment")
def test_real_ffmpeg_stream_copy_concatenates_two_segments(tmp_path):
    """Real ffmpeg (no mocking): two same-params mp4s built with the repo's
    own encode helper, stitched with overlap=0, output frame count = sum of
    both, and no decode path is invoked (frame_reader/encode both would
    raise if called)."""
    from src.pipelines.pipes._shared.media.video_encode import encode_frames_to_mp4

    frames_a = np.zeros((3, 4, 4, 3), dtype=np.uint8)
    frames_b = np.full((2, 4, 4, 3), 200, dtype=np.uint8)
    seg0 = tmp_path / "seg0.mp4"
    seg1 = tmp_path / "seg1.mp4"
    encode_frames_to_mp4(frames_a, seg0, fps=10.0)
    encode_frames_to_mp4(frames_b, seg1, fps=10.0)

    out_path = tmp_path / "out.mp4"

    def fail(*a, **kw):
        raise AssertionError("decode/encode path must not run when stream-copy succeeds")

    stitch_segments([seg0, seg1], overlap=0, out_path=out_path, fps=10.0,
                     frame_reader=fail, encode=fail)

    cv2 = pytest.importorskip("cv2", reason="cv2 not available in this environment", exc_type=ImportError)
    cap = cv2.VideoCapture(str(out_path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert total == 5


# -- real cv2 round trip (guarded) ------------------------------------------
#
# NOTE: `pytest.importorskip` at MODULE level would skip every test in this
# file (it raises `Skipped` during module exec, before collection finishes),
# not just the cv2-dependent one below -- so the skip is scoped to just this
# one test via a local import instead.

def _write_video(cv2, path, colors, size=8, fps=10.0):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (size, size))
    assert writer.isOpened()
    try:
        for r, g, b in colors:
            frame = np.zeros((size, size, 3), dtype=np.uint8)
            frame[:, :, 0], frame[:, :, 1], frame[:, :, 2] = b, g, r
            writer.write(frame)
    finally:
        writer.release()


def test_real_cv2_default_reader_drops_overlap(tmp_path):
    cv2 = pytest.importorskip("cv2", reason="cv2 not available in this environment", exc_type=ImportError)
    seg0 = tmp_path / "seg0.mp4"
    seg1 = tmp_path / "seg1.mp4"
    _write_video(cv2, seg0, [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    _write_video(cv2, seg1, [(1, 1, 1), (2, 2, 2), (255, 255, 0)])

    for path in (seg0, seg1):
        cap = cv2.VideoCapture(str(path))
        ok, _ = cap.read()
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        if not ok or total < 3:
            pytest.skip("cv2 VideoWriter/VideoCapture round-trip not functional in this environment")

    out_path = tmp_path / "stitched.mp4"
    captured = {}
    stitch_segments([seg0, seg1], overlap=2, out_path=out_path, fps=10.0,
                     encode=lambda frames, p, fps: captured.update(frames=frames))

    # seg0 kept whole (3 frames), seg1 drops its first 2 -> 1 frame. Total 4.
    assert captured["frames"].shape[0] == 4
