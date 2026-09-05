"""Temp-file ownership and cancellation contract for interpolator/rife
(INF-R03): every attempt file the pipe creates is either published as the
final gallery output or removed, never left behind on cancellation, a model
failure, a write/close failure, or a failed mux.

Drives `process` with a fake `cv2.VideoCapture` instead of a decoded file, so
every scenario is deterministic and needs no ffmpeg/codec support. The fake
`StreamingMp4Writer` still creates/removes a REAL file at the path it is
given, so temp-directory assertions exercise the pipe's own path bookkeeping,
not the fake's."""

import tempfile
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
cv2 = pytest.importorskip(
    "cv2", reason="cv2 (or its native deps) not available", exc_type=ImportError,
)

from src.pipelines.contracts import PipeInput
from src.pipelines.outputs import GalleryGenerationOutput
from src.pipelines.pipes.interpolator.rife import main as rife_main
from src.pipelines.pipes.interpolator.rife.main import RifeInterpolatorPipe
from src.platform.runtime.native.errors import SamplingCancelled
from tests.vendor.rife.layouts import NARROW_NO_ENCODER_BLOCKS
from vendor.rife.ifnet import IFNet


def _stub_load_model(_path, device, _models=None):
    torch.manual_seed(0)
    model = IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval().to(device)
    return model.half() if device == "cuda" else model.float()


@pytest.fixture
def stub_model(monkeypatch):
    monkeypatch.setattr(rife_main, "_load_model", _stub_load_model)


@pytest.fixture
def scratch_tempdir(tmp_path, monkeypatch):
    """Point tempfile.NamedTemporaryFile at a directory this test owns, so
    "what does the pipe leave behind" can be asserted directly."""
    d = tmp_path / "attempt"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


class _FakeCapture:
    """Deterministic stand-in for cv2.VideoCapture: serves `n_frames` solid
    RGB frames, then EOF."""

    def __init__(self, n_frames=6, size=(8, 8), fps=10.0):
        self.n_frames = n_frames
        self.size = size
        self.fps = fps
        self._served = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.n_frames
        return 0

    def read(self):
        if self._served >= self.n_frames:
            return False, None
        self._served += 1
        w, h = self.size
        frame = np.full((h, w, 3), self._served % 255, dtype=np.uint8)
        return True, frame

    def release(self):
        self.released = True


class _RealFileFakeWriter:
    """Fakes the encode but still creates/removes a real file at `out_path`,
    so temp-directory assertions exercise the pipe's own cleanup rather than
    needing a working ffmpeg."""

    instances = []

    def __init__(self, out_path, width, height, fps, **kwargs):
        self.out_path = Path(out_path)
        self.out_path.write_bytes(b"video")
        self.width, self.height, self.fps = width, height, fps
        self.frames = []
        self.closed = False
        self.aborted = False
        self.close_should_fail = False
        _RealFileFakeWriter.instances.append(self)

    def write(self, frame):
        self.frames.append(np.asarray(frame))

    def close(self):
        self.closed = True
        if self.close_should_fail:
            raise RuntimeError("ffmpeg encode failed (exit 1): stub")

    def abort(self):
        self.aborted = True


class _WriteFailsWriter(_RealFileFakeWriter):
    """Fails on the 3rd frame written, simulating ffmpeg dying mid-encode."""

    def write(self, frame):
        if len(self.frames) >= 2:
            raise RuntimeError("ffmpeg died mid-encode (exit 1): stub")
        super().write(frame)


def _install(monkeypatch, capture, writer_cls=_RealFileFakeWriter, mux=False):
    monkeypatch.setattr(cv2, "VideoCapture", lambda _path: capture)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", writer_cls)
    _RealFileFakeWriter.instances.clear()
    monkeypatch.setattr(
        rife_main, "mux_audio_from_source", mux if callable(mux) else (lambda *a, **k: mux)
    )


def _pipe(**config):
    return RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2, **config})


def _run(pipe, outputs, is_cancelled=None):
    return pipe.process(
        PipeInput(input={"video": ["stub.mp4"]}), outputs.append, is_cancelled=is_cancelled,
    )


def _no_gallery(outputs):
    return not any(isinstance(o, GalleryGenerationOutput) for o in outputs)


# -- cancellation --------------------------------------------------------

def test_cancel_after_a_frame_pair_leaves_no_files_and_aborts(
    scratch_tempdir, monkeypatch, stub_model,
):
    capture = _FakeCapture(n_frames=6)
    _install(monkeypatch, capture, mux=False)
    pipe = _pipe(keep_audio=False)
    outputs = []

    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3  # after a couple of pairs have been processed

    with pytest.raises(SamplingCancelled):
        _run(pipe, outputs, is_cancelled=cancel)

    assert _no_gallery(outputs)
    writer = _RealFileFakeWriter.instances[0]
    assert writer.aborted and not writer.closed
    assert list(scratch_tempdir.iterdir()) == []
    assert capture.released


def test_cancel_at_final_boundary_discards_the_finished_clip(
    scratch_tempdir, monkeypatch, stub_model,
):
    # Cancellation observed only once the frame loop and the mux are both
    # done ("probe flips right before publication") must still keep the
    # finished clip out of the gallery and off disk.
    capture = _FakeCapture(n_frames=4)
    cancel_flag = {"v": False}

    def fake_mux(video_only, source, out_path):
        Path(out_path).write_bytes(b"muxed")
        cancel_flag["v"] = True
        return True

    _install(monkeypatch, capture, mux=fake_mux)
    pipe = _pipe(keep_audio=True)
    outputs = []

    with pytest.raises(SamplingCancelled):
        _run(pipe, outputs, is_cancelled=lambda: cancel_flag["v"])

    assert _no_gallery(outputs)
    assert _RealFileFakeWriter.instances[0].closed
    assert list(scratch_tempdir.iterdir()) == []


# -- failures --------------------------------------------------------------

def test_model_failure_aborts_the_writer_and_cleans_up(
    scratch_tempdir, monkeypatch, stub_model,
):
    capture = _FakeCapture(n_frames=4)
    _install(monkeypatch, capture, mux=False)

    def boom(*_a, **_k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(RifeInterpolatorPipe, "_prepare_pair", staticmethod(boom))
    pipe = _pipe(keep_audio=False)
    outputs = []

    with pytest.raises(RuntimeError, match="model exploded"):
        _run(pipe, outputs)

    assert _no_gallery(outputs)
    writer = _RealFileFakeWriter.instances[0]
    assert writer.aborted
    assert list(scratch_tempdir.iterdir()) == []


def test_write_failure_aborts_the_writer_and_cleans_up(
    scratch_tempdir, monkeypatch, stub_model,
):
    capture = _FakeCapture(n_frames=6)
    _install(monkeypatch, capture, writer_cls=_WriteFailsWriter, mux=False)
    pipe = _pipe(keep_audio=False)
    outputs = []

    with pytest.raises(RuntimeError, match="ffmpeg died mid-encode"):
        _run(pipe, outputs)

    assert _no_gallery(outputs)
    writer = _RealFileFakeWriter.instances[0]
    assert writer.aborted
    assert list(scratch_tempdir.iterdir()) == []


def test_close_failure_aborts_and_cleans_up(scratch_tempdir, monkeypatch, stub_model):
    capture = _FakeCapture(n_frames=4)

    def make_writer(out_path, width, height, fps, **kwargs):
        w = _RealFileFakeWriter(out_path, width, height, fps, **kwargs)
        w.close_should_fail = True
        return w

    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: capture)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", make_writer)
    _RealFileFakeWriter.instances.clear()
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)
    pipe = _pipe(keep_audio=False)
    outputs = []

    with pytest.raises(RuntimeError, match="ffmpeg encode failed"):
        _run(pipe, outputs)

    assert _no_gallery(outputs)
    writer = _RealFileFakeWriter.instances[0]
    assert writer.closed  # close() was attempted and is what raised
    assert writer.aborted  # then aborted by the except handler (idempotent on an already-exited proc)
    assert list(scratch_tempdir.iterdir()) == []


# -- mux outcomes and ordinary EOF -----------------------------------------

def test_failed_mux_keeps_only_the_video_only_output(scratch_tempdir, monkeypatch, stub_model):
    capture = _FakeCapture(n_frames=4)
    _install(monkeypatch, capture, mux=False)  # mirrors mux_audio_from_source's own contract:
    pipe = _pipe(keep_audio=True)               # a failed attempt never leaves a file behind.
    outputs = []

    result = _run(pipe, outputs)

    assert not _no_gallery(outputs)
    final = Path(result.output["video"][0])
    assert final.exists()
    assert set(scratch_tempdir.iterdir()) == {final}


def test_successful_mux_removes_the_intermediate(scratch_tempdir, monkeypatch, stub_model):
    capture = _FakeCapture(n_frames=4)

    def fake_mux(video_only, source, out_path):
        Path(out_path).write_bytes(b"muxed")
        return True

    _install(monkeypatch, capture, mux=fake_mux)
    pipe = _pipe(keep_audio=True)
    outputs = []

    result = _run(pipe, outputs)
    final = Path(result.output["video"][0])

    assert final != _RealFileFakeWriter.instances[0].out_path
    assert set(scratch_tempdir.iterdir()) == {final}


def test_ordinary_eof_leaves_only_the_final_output(scratch_tempdir, monkeypatch, stub_model):
    capture = _FakeCapture(n_frames=4)
    _install(monkeypatch, capture, mux=False)
    pipe = _pipe(keep_audio=False)
    outputs = []

    result = _run(pipe, outputs)
    final = Path(result.output["video"][0])

    assert not _no_gallery(outputs)
    assert set(scratch_tempdir.iterdir()) == {final}
    assert capture.released
