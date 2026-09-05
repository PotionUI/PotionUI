"""interpolator/rife cleanup-ordering fixes (Codex review of a113bbe +
3587f53):

1. The video capture is owned from the moment it is acquired, not from
   whatever point the old code happened to enter the try/finally that used
   to release it -- a capture-open validation failure or a temp-file-create
   failure used to bypass release entirely.
2. Every release step in the failure-cleanup path runs independently: one
   failing (e.g. `writer.abort()` itself raising) must never skip the other,
   or replace the exception it was handling."""

import shutil
import tempfile

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

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH",
)


def _stub_load_model(_path, device, _models=None):
    torch.manual_seed(0)
    model = IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval().to(device)
    return model.half() if device == "cuda" else model.float()


@pytest.fixture
def stub_model(monkeypatch):
    monkeypatch.setattr(rife_main, "_load_model", _stub_load_model)


def _pipe(**config):
    return RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2, **config})


def _no_gallery(outputs):
    return not any(isinstance(o, GalleryGenerationOutput) for o in outputs)


# -- item 1: capture ownership starts at acquisition, not at first use ------

class _NeverOpensCapture:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return False

    def release(self):
        self.released = True


def test_capture_released_when_isOpened_returns_false(monkeypatch, stub_model):
    cap = _NeverOpensCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: cap)
    pipe = _pipe()

    with pytest.raises(ValueError, match="could not open video"):
        pipe.process(PipeInput(input={"video": ["missing.mp4"]}), lambda o: None)

    assert cap.released


class _OpensCapture:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return True

    def release(self):
        self.released = True


def test_capture_released_when_temp_file_creation_fails(monkeypatch, stub_model):
    cap = _OpensCapture()
    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: cap)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(rife_main.tempfile, "NamedTemporaryFile", boom)
    pipe = _pipe()

    with pytest.raises(OSError, match="disk full"):
        pipe.process(PipeInput(input={"video": ["in.mp4"]}), lambda o: None)

    assert cap.released


# -- item 2: independent cleanup, originating exception never masked --------

class _DecodingCapture:
    """Serves `n_frames` solid frames, then EOF -- enough for the pipe to
    create a writer and reach the per-pair helper for a mid-clip failure."""

    def __init__(self, n_frames=4, size=(8, 8)):
        self.n_frames = n_frames
        self.size = size
        self._served = 0

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return 10.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return self.n_frames
        return 0

    def read(self):
        if self._served >= self.n_frames:
            return False, None
        self._served += 1
        w, h = self.size
        return True, np.zeros((h, w, 3), dtype=np.uint8)

    def release(self):
        pass


class _AbortRaisesWriter:
    """A writer whose abort() itself raises -- the failure this pipe's own
    cleanup must survive without giving up on deleting owned files, and
    without letting THIS exception replace the original one."""

    def __init__(self, out_path, width, height, fps, **kwargs):
        from pathlib import Path
        self.out_path = Path(out_path)
        self.out_path.write_bytes(b"partial")

    def write(self, frame):
        pass

    def close(self):
        pass

    def abort(self):
        raise RuntimeError("abort itself blew up")


def test_abort_failure_does_not_skip_temp_file_cleanup(tmp_path, monkeypatch, stub_model):
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: _DecodingCapture())
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _AbortRaisesWriter)

    def boom(*_a, **_k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(RifeInterpolatorPipe, "_prepare_pair", staticmethod(boom))
    pipe = _pipe(keep_audio=False)
    outputs = []

    # The ORIGINAL failure ("model exploded") must be what propagates, not
    # the abort() failure ("abort itself blew up") that fires while handling
    # it.
    with pytest.raises(RuntimeError, match="model exploded"):
        pipe.process(PipeInput(input={"video": ["in.mp4"]}), outputs.append)

    assert _no_gallery(outputs)
    # abort() raised before ever reaching _discard() in the old code path --
    # the fix must still have removed the writer's file.
    assert list(tmp_path.iterdir()) == []


# -- item 3, end to end: a REAL StreamingMp4Writer/ffmpeg process, cancelled -

def _write_input_video(path, n_frames=10, fps=10.0, size=(64, 64)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        pytest.skip("no writable mp4 codec available for the synthetic input")
    rng = np.random.default_rng(0)
    for _ in range(n_frames):
        writer.write(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
    writer.release()
    cap = cv2.VideoCapture(str(path))
    decoded = 0
    while cap.isOpened():
        ok, _ = cap.read()
        if not ok:
            break
        decoded += 1
    cap.release()
    if decoded < 3:
        pytest.skip("synthetic input video is not decodable in this environment")
    return decoded


@needs_ffmpeg
def test_real_ffmpeg_cancellation_leaves_no_stray_files_or_gallery(tmp_path, monkeypatch, stub_model):
    # No fakes anywhere in the encode path: a real ffmpeg child, a real
    # StreamingMp4Writer, cancelled mid-clip -- proves the abort()
    # reordering and stderr drain fix hold up against the real process, not
    # just the fake models built to exercise them in isolation.
    scratch = tmp_path / "attempt"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    video = tmp_path / "in.mp4"
    _write_input_video(video, n_frames=10)

    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2, "keep_audio": False})
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    outputs = []
    with pytest.raises(SamplingCancelled):
        pipe.process(PipeInput(input={"video": [str(video)]}), outputs.append, is_cancelled=cancel)

    assert _no_gallery(outputs)
    assert list(scratch.iterdir()) == []
