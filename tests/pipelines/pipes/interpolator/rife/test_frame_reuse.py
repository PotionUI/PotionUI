"""The rife pipe prepares each source frame once per clip, not once per output.

`process` streams pairs and already carried the previous frame's tensor over as
the next pair's left frame; these tests cover carrying its *prepared* form
(padding + encoder features) over too, so a clip of N frames costs N
preparations instead of 2 x (N-1) x (factor-1).

The helper-level tests are pure torch and run everywhere; only the ones that
decode a video through `process` are gated on cv2, which imports but fails to
load its shared objects in some containers.
"""

import weakref

import numpy as np
import pytest

torch = pytest.importorskip("torch")

try:  # cv2 imports but fails to load its shared objects in some containers.
    import cv2
except ImportError:
    cv2 = None

# Only the tests that decode a video need cv2; the per-pair helper is pure torch
# and must stay covered on boxes where cv2 cannot load.
needs_cv2 = pytest.mark.skipif(cv2 is None, reason="cv2 (or its native deps) not available")

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.interpolator.rife import main as rife_main
from src.pipelines.pipes.interpolator.rife.main import RifeInterpolatorPipe
from src.platform.runtime.native.errors import SamplingCancelled
from tests.vendor.rife.layouts import NARROW_ENCODER_BLOCKS, NARROW_NO_ENCODER_BLOCKS
from vendor.rife.ifnet import IFNet
from vendor.rife.inference import prepare_frame

FACTOR = 4
TIMESTEPS = (0.25, 0.5, 0.75)


def _model(with_encoder=True, seed=0):
    torch.manual_seed(seed)
    specs = NARROW_ENCODER_BLOCKS if with_encoder else NARROW_NO_ENCODER_BLOCKS
    return IFNet(specs, (16, 4) if with_encoder else None).eval().float()


def _count_encodes(model):
    calls = {"n": 0}
    if model.encode is not None:
        model.encode.register_forward_hook(
            lambda *_: calls.__setitem__("n", calls["n"] + 1)
        )
    return calls


def _frames(n=3, h=64, w=64, seed=1):
    generator = torch.Generator().manual_seed(seed)
    return [torch.rand(1, 3, h, w, generator=generator) for _ in range(n)]


def _stream(model, frames, reuse):
    """Drive the pipe's per-pair helper the way `process` does, with and without
    carrying the previous pair's right frame over."""
    out = []
    carried = None
    for img0, img1 in zip(frames, frames[1:]):
        f0, f1 = RifeInterpolatorPipe._prepare_pair(
            model, img0, img1, 1.0, carried if reuse else None
        )
        out.extend(RifeInterpolatorPipe._run(model, f0, f1, t, 1.0) for t in TIMESTEPS)
        carried = f1
    return out


class _FakeWriter:
    last = None

    def __init__(self, out_path, width, height, fps, **kwargs):
        self.fps = fps
        self.frames = []
        self.aborted = False
        _FakeWriter.last = self

    def write(self, frame):
        self.frames.append(np.asarray(frame))

    def close(self):
        pass

    def abort(self):
        self.aborted = True


def _write_input_video(path, n_frames, fps=10.0, size=(64, 64)):
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


def _force_cpu(monkeypatch):
    # The tiny models here are built on the CPU and the box may have a GPU; the
    # pipe moves decoded frames to whatever device it picks, so pin the choice.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


def _run_pipe(tmp_path, monkeypatch, model, video, factor=FACTOR):
    _force_cpu(monkeypatch)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)
    monkeypatch.setattr(rife_main, "_load_model", lambda _path, _device, _models=None: model)
    pipe = RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": factor,
                                 "keep_audio": False})
    pipe.process(PipeInput(input={"video": [str(video)]}), lambda o: None)
    return _FakeWriter.last.frames


# -- the per-pair helper ------------------------------------------------------

def test_helper_reuse_produces_identical_frames():
    model = _model()
    frames = _frames(n=3)

    reused = _stream(model, frames, reuse=True)
    fresh = _stream(model, frames, reuse=False)

    assert len(reused) == len(fresh) == 6
    for got, want in zip(reused, fresh):
        assert np.array_equal(got, want)


def test_helper_encodes_each_source_frame_once():
    frames = _frames(n=3)

    fresh_model = _model()
    fresh = _count_encodes(fresh_model)
    _stream(fresh_model, frames, reuse=False)

    reused_model = _model()
    reused = _count_encodes(reused_model)
    _stream(reused_model, frames, reuse=True)

    # Without reuse each pair prepares both its frames: 2 pairs x 2 frames = 4
    # (forward's own per-call encode would make it 2 x 3 x 2 = 12). With reuse
    # the middle frame is prepared once, so 3 source frames = 3 encodes.
    assert fresh["n"] == 4
    assert reused["n"] == 3


def test_helper_reprepares_when_geometry_changes():
    model = _model()
    small = _frames(n=1, h=64, w=64)[0]
    large = _frames(n=1, h=96, w=96)[0]
    stale = prepare_frame(model, large, 1.0)

    f0, _ = RifeInterpolatorPipe._prepare_pair(model, small, small, 1.0, stale)

    assert f0 is not stale
    assert f0.size == (64, 64)


def test_helper_reprepares_when_the_model_changes():
    img = _frames(n=1)[0]
    stale = prepare_frame(_model(), img, 1.0)
    other = _model()

    f0, _ = RifeInterpolatorPipe._prepare_pair(other, img, img, 1.0, stale)

    assert f0 is not stale
    assert f0.model_id == id(other)


def test_helper_reprepares_when_flow_scale_changes():
    model = _model()
    img = _frames(n=1)[0]
    stale = prepare_frame(model, img, 1.0)

    f0, _ = RifeInterpolatorPipe._prepare_pair(model, img, img, 0.5, stale)

    assert f0 is not stale
    assert f0.flow_scale == 0.5


def test_helper_reuses_the_carried_frame_object():
    model = _model()
    img = _frames(n=1)[0]
    carried = prepare_frame(model, img, 1.0)

    f0, _ = RifeInterpolatorPipe._prepare_pair(model, img, img, 1.0, carried)

    assert f0 is carried


# -- through `process` --------------------------------------------------------

@needs_cv2
def test_pipe_prepares_each_decoded_frame_once(tmp_path, monkeypatch):
    video = tmp_path / "in.mp4"
    decoded = _write_input_video(video, n_frames=4)
    model = _model()
    calls = _count_encodes(model)

    frames = _run_pipe(tmp_path, monkeypatch, model, video)

    assert len(frames) == RifeInterpolatorPipe.output_frame_count(decoded, FACTOR)
    assert calls["n"] == decoded


@needs_cv2
def test_pipe_output_is_unchanged_by_reuse(tmp_path, monkeypatch):
    video = tmp_path / "in.mp4"
    _write_input_video(video, n_frames=4)

    with_reuse = _run_pipe(tmp_path, monkeypatch, _model(), video)

    fresh = staticmethod(
        lambda model, img0, img1, flow_scale, carried=None: (
            prepare_frame(model, img0, flow_scale),
            prepare_frame(model, img1, flow_scale),
        )
    )
    monkeypatch.setattr(RifeInterpolatorPipe, "_prepare_pair", fresh)
    without_reuse = _run_pipe(tmp_path, monkeypatch, _model(), video)

    assert len(with_reuse) == len(without_reuse)
    for got, want in zip(with_reuse, without_reuse):
        assert np.array_equal(got, want)


@needs_cv2
def test_pipe_never_encodes_for_rife46(tmp_path, monkeypatch):
    video = tmp_path / "in.mp4"
    model = _model(with_encoder=False)
    calls = _count_encodes(model)

    _write_input_video(video, n_frames=4)
    _run_pipe(tmp_path, monkeypatch, model, video)

    assert model.encode is None
    assert calls["n"] == 0


@needs_cv2
def test_pipe_releases_prepared_frames_on_cancellation(tmp_path, monkeypatch):
    # Padded frames and encoder features are the biggest tensors the loop holds;
    # a cancelled clip must not leave them referenced by the frame it stopped on.
    import gc
    import weakref

    video = tmp_path / "in.mp4"
    _write_input_video(video, n_frames=6)
    model = _model()
    seen = []

    original = RifeInterpolatorPipe._prepare_pair

    def spy(m, img0, img1, flow_scale, carried=None):
        f0, f1 = original(m, img0, img1, flow_scale, carried)
        seen.extend(weakref.ref(f.padded) for f in (f0, f1))
        return f0, f1

    _force_cpu(monkeypatch)
    monkeypatch.setattr(RifeInterpolatorPipe, "_prepare_pair", staticmethod(spy))
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)
    monkeypatch.setattr(rife_main, "_load_model", lambda _p, _d, _m=None: model)

    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    with pytest.raises(SamplingCancelled):
        RifeInterpolatorPipe({"model": {"file_path": "x"}, "factor": 2}).process(
            PipeInput(input={"video": [str(video)]}), lambda o: None, is_cancelled=cancel
        )

    assert seen, "the cancelled run never prepared a pair"
    assert _FakeWriter.last.aborted
    gc.collect()
    assert all(ref() is None for ref in seen)


# -- how many prepared frames the clip keeps alive ----------------------------

class _PreparedProbe:
    """Counts the live `PreparedFrame` objects and their tensor bytes at every
    preparation, so the maximum over a clip is the loop's real high-water mark.
    Weakrefs, so a frame the loop released stops counting immediately -- the
    dataclass is acyclic and refcounting frees it without a gc pass."""

    def __init__(self):
        self.refs = []
        self.peak_live = 0
        self.peak_bytes = 0

    def install(self, monkeypatch):
        original = rife_main.prepare_frame

        def prepare(model, img, flow_scale=1.0):
            frame = original(model, img, flow_scale)
            self.refs.append(weakref.ref(frame))
            live = [f for f in (ref() for ref in self.refs) if f is not None]
            self.peak_live = max(self.peak_live, len(live))
            self.peak_bytes = max(self.peak_bytes, sum(_frame_bytes(f) for f in live))
            return frame

        monkeypatch.setattr(rife_main, "prepare_frame", prepare)
        return self


def _frame_bytes(frame):
    return sum(
        t.numel() * t.element_size()
        for t in (frame.padded, frame.features)
        if t is not None
    )


@needs_cv2
@pytest.mark.parametrize("with_encoder", [True, False])
def test_pipe_keeps_at_most_two_prepared_frames_alive(tmp_path, monkeypatch, with_encoder):
    # The reuse is only worth having if it bounds memory as well as work: the
    # previous pair's left frame must be released before the next right frame is
    # allocated, or the clip briefly holds three.
    video = tmp_path / "in.mp4"
    decoded = _write_input_video(video, n_frames=6)
    model = _model(with_encoder=with_encoder)
    probe = _PreparedProbe().install(monkeypatch)

    frames = _run_pipe(tmp_path, monkeypatch, model, video)

    one = _frame_bytes(prepare_frame(model, torch.zeros(1, 3, 64, 64), 1.0))
    assert len(frames) == RifeInterpolatorPipe.output_frame_count(decoded, FACTOR)
    assert len(probe.refs) == decoded
    assert probe.peak_live == 2
    assert probe.peak_bytes == 2 * one


@needs_cv2
def test_pipe_holds_no_prepared_frames_after_the_clip(tmp_path, monkeypatch):
    video = tmp_path / "in.mp4"
    _write_input_video(video, n_frames=6)
    probe = _PreparedProbe().install(monkeypatch)

    _run_pipe(tmp_path, monkeypatch, _model(), video)

    assert probe.refs
    assert all(ref() is None for ref in probe.refs)
