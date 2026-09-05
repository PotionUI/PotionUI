"""RIFE checkpoint lifecycle (INF-R02): the old ``_MODEL_CACHE`` was a
process-global dict keyed on ``(path, mtime)`` that nothing ever evicted or
offloaded -- switching models, or reloading a changed checkpoint at the same
path, accumulated entries (GPU-resident ones included) forever.

``_acquire_base_model``/``_load_model`` now go through the injected ``MODELS``
lifecycle service (a real ``ModelLifecycle`` here, exercising its actual
fingerprint-bust/reuse behaviour rather than a fake standing in for it), keyed
on the resolved path and fingerprinted on the checkpoint's on-disk revision
(mtime + size). ``_idle_model`` returns the active model to CPU/fp32 -- tested
directly, and through `process()` on every exit path (success, mid-clip
failure, cancellation) via a ``_FakeModels`` double mirroring
``matting/birefnet``'s test convention (pins one model, records the
key/fingerprint each acquire call used)."""

import gc
import os
import time
import weakref

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
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from tests.vendor.rife.layouts import NARROW_NO_ENCODER_BLOCKS
from vendor.rife.ifnet import IFNet


def _checkpoint(tmp_path, name="rife.pth", content=b"stub-checkpoint"):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def _lifecycle():
    return ModelLifecycle(gpu_monitor=None, settings=None)


def _force_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)


# -- _acquire_base_model, against a REAL ModelLifecycle ----------------------

def test_unchanged_reuse_loads_once(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    models = _lifecycle()
    calls = {"n": 0}

    def fake_load(_path, device):
        calls["n"] += 1
        return IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()

    monkeypatch.setattr(rife_main, "load_ifnet", fake_load)

    first = rife_main._acquire_base_model(models, path)
    second = rife_main._acquire_base_model(models, path)

    assert calls["n"] == 1
    assert first is second


def test_a_b_a_switch_keeps_a_bounded_retained_count(tmp_path, monkeypatch):
    path_a = _checkpoint(tmp_path, "a.pth")
    path_b = _checkpoint(tmp_path, "b.pth")
    models = _lifecycle()
    calls = {"a": 0, "b": 0}

    def fake_load(p, device):
        calls["a" if p == path_a else "b"] += 1
        return IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()

    monkeypatch.setattr(rife_main, "load_ifnet", fake_load)

    a1 = rife_main._acquire_base_model(models, path_a)
    rife_main._acquire_base_model(models, path_b)
    a2 = rife_main._acquire_base_model(models, path_a)

    assert a1 is a2  # the switch back to A is a cache hit, not a reload
    assert calls == {"a": 1, "b": 1}
    assert len(models._entries) == 2  # exactly {A, B} -- no third, unbounded entry


def test_same_path_revision_replaces_the_obsolete_module(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    models = _lifecycle()
    calls = {"n": 0}

    def fake_load(_p, device):
        calls["n"] += 1
        return IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()

    monkeypatch.setattr(rife_main, "load_ifnet", fake_load)

    first = rife_main._acquire_base_model(models, path)
    stale_ref = weakref.ref(first)
    del first  # the cache must be the sole remaining owner for it to be collectible

    # Bump the on-disk revision (mtime + size) without changing the path --
    # e.g. the checkpoint was re-downloaded or swapped in place.
    time.sleep(0.01)
    with open(path, "ab") as f:
        f.write(b"more-bytes")
    os.utime(path, None)

    second = rife_main._acquire_base_model(models, path)

    assert calls["n"] == 2
    assert len(models._entries) == 1  # the stale entry was replaced, not appended
    gc.collect()
    assert stale_ref() is None


def test_fallback_cache_is_a_single_entry_not_a_second_global(tmp_path, monkeypatch):
    """No MODELS service injected (isolated pipe use): still reuses across
    calls for the SAME path, still bounded to one entry when the path
    changes -- never a second, unbounded global alongside the lifecycle."""
    monkeypatch.setattr(rife_main, "_FALLBACK_MODEL", {})
    path_a = _checkpoint(tmp_path, "a.pth")
    path_b = _checkpoint(tmp_path, "b.pth")
    calls = {"a": 0, "b": 0}

    def fake_load(p, device):
        calls["a" if p == path_a else "b"] += 1
        return IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()

    monkeypatch.setattr(rife_main, "load_ifnet", fake_load)

    first = rife_main._acquire_base_model(None, path_a)
    second = rife_main._acquire_base_model(None, path_a)
    assert first is second
    assert calls["a"] == 1

    rife_main._acquire_base_model(None, path_b)
    assert len(rife_main._FALLBACK_MODEL) == 3  # {"key", "fingerprint", "module"} -- one entry

    # Switching back to A after B is now a fresh load: the fallback holds only
    # the single most-recent entry, not both.
    rife_main._acquire_base_model(None, path_a)
    assert calls == {"a": 2, "b": 1}


# -- _idle_model --------------------------------------------------------------

class _RecordingModule:
    def __init__(self):
        self.calls = []

    def to(self, device):
        self.calls.append(("to", device))
        return self

    def float(self):
        self.calls.append(("float",))
        return self


def test_idle_model_moves_to_cpu_and_fp32():
    module = _RecordingModule()

    rife_main._idle_model(module)

    assert module.calls == [("to", "cpu"), ("float",)]


# -- process(): release on every exit path, via a pinned _FakeModels --------
# Mirrors matting/birefnet's test convention: `_FakeModels.acquire` pins one
# model and records the (key, fingerprint) each call used, so these tests
# exercise the release CONTRACT (moved to device, always returned to CPU)
# without needing a real MODELS cache decision.

class _FakeModels:
    def __init__(self, model):
        self.model = model
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return self.model


class _TrackingIFNet(IFNet):
    """A real, usable IFNet that also records every device/dtype move, so a
    test can assert the placement SEQUENCE (acquired -> moved to compute
    device -> ... -> idled back to CPU/fp32) around a real frame loop."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.device_calls = []

    def to(self, device, *a, **k):
        self.device_calls.append(("to", str(device)))
        return super().to(device, *a, **k)

    def half(self):
        self.device_calls.append(("half",))
        return super().half()

    def float(self):
        self.device_calls.append(("float",))
        return super().float()


class _FakeWriter:
    last = None

    def __init__(self, out_path, width, height, fps, **kwargs):
        self.frames = []
        self.aborted = False
        _FakeWriter.last = self

    def write(self, frame):
        self.frames.append(np.asarray(frame))

    def close(self):
        pass

    def abort(self):
        self.aborted = True


def _write_input_video(path, n_frames=6, fps=10.0, size=(64, 64)):
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


def _setup(monkeypatch, tmp_path):
    _force_cpu(monkeypatch)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    monkeypatch.setattr(rife_main, "mux_audio_from_source", lambda *a, **k: False)
    model = _TrackingIFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()
    models = _FakeModels(model)
    video = tmp_path / "in.mp4"
    _write_input_video(video)
    checkpoint = _checkpoint(tmp_path)
    return model, models, video, checkpoint


def test_process_keeps_the_model_stable_and_idles_it_after_success(tmp_path, monkeypatch):
    model, models, video, checkpoint = _setup(monkeypatch, tmp_path)
    pipe = RifeInterpolatorPipe({"model": {"file_path": checkpoint}, "factor": 2, "keep_audio": False})

    result = pipe.process(
        PipeInput(input={"video": [str(video)], "MODELS": models}), lambda o: None,
    )

    assert len(models.calls) == 1  # acquired once for the whole clip, not per frame/pair
    assert "video" in result.output
    # Moved to the compute device once, then returned to idle CPU/fp32 --
    # never left resident.
    assert model.device_calls[0] == ("to", "cpu")  # CPU box: device == idle already
    assert model.device_calls[-2:] == [("to", "cpu"), ("float",)]


def test_process_idles_the_model_on_a_mid_clip_failure(tmp_path, monkeypatch):
    model, models, video, checkpoint = _setup(monkeypatch, tmp_path)

    def boom(*_a, **_k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(RifeInterpolatorPipe, "_prepare_pair", staticmethod(boom))
    pipe = RifeInterpolatorPipe({"model": {"file_path": checkpoint}, "factor": 2, "keep_audio": False})

    with pytest.raises(RuntimeError, match="model exploded"):
        pipe.process(PipeInput(input={"video": [str(video)], "MODELS": models}), lambda o: None)

    assert model.device_calls[-2:] == [("to", "cpu"), ("float",)]


def test_process_idles_the_model_on_cancellation(tmp_path, monkeypatch):
    model, models, video, checkpoint = _setup(monkeypatch, tmp_path)
    pipe = RifeInterpolatorPipe({"model": {"file_path": checkpoint}, "factor": 2, "keep_audio": False})

    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2

    outputs = []
    from src.platform.runtime.native.errors import SamplingCancelled
    with pytest.raises(SamplingCancelled):
        pipe.process(
            PipeInput(input={"video": [str(video)], "MODELS": models}),
            outputs.append, is_cancelled=cancel,
        )

    assert not any(isinstance(o, GalleryGenerationOutput) for o in outputs)
    assert model.device_calls[-2:] == [("to", "cpu"), ("float",)]


def test_process_idles_the_model_when_the_video_cannot_be_opened(tmp_path, monkeypatch):
    # A failure BEFORE the frame loop even starts must still release the model
    # -- the acquire/placement happens before the video is ever opened.
    _force_cpu(monkeypatch)
    model = _TrackingIFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()
    models = _FakeModels(model)
    checkpoint = _checkpoint(tmp_path)
    monkeypatch.setattr(cv2, "VideoCapture", lambda _p: type("C", (), {"isOpened": lambda self: False})())
    pipe = RifeInterpolatorPipe({"model": {"file_path": checkpoint}, "factor": 2, "keep_audio": False})

    with pytest.raises(ValueError, match="could not open video"):
        pipe.process(PipeInput(input={"video": ["missing.mp4"], "MODELS": models}), lambda o: None)

    assert model.device_calls[-2:] == [("to", "cpu"), ("float",)]


# -- failed placement/cast: invalidate the cache entry, never mask the error -
# A `.to()`/`.half()`/`.float()` that raises partway can leave the CACHED
# object's parameters split across devices/dtypes. `_load_model` must not
# hand that object back to a later acquire as if placement had succeeded.

class _PartialPlacementModule:
    """`.to()` records the attempted move, then raises -- as a CUDA OOM or an
    interrupted transfer would leave a real module part-moved."""

    def __init__(self):
        self.to_calls = []

    def to(self, device):
        self.to_calls.append(device)
        raise RuntimeError("placement failed partway")

    def parameters(self):
        return iter([torch.zeros(1)])


class _HalfCastFailsModule:
    """`.to()` succeeds; `.half()` (the CUDA-only cast step) raises partway."""

    def __init__(self):
        self.calls = []

    def to(self, device):
        self.calls.append(("to", device))
        return self

    def half(self):
        self.calls.append(("half",))
        raise RuntimeError("half cast failed partway")

    def parameters(self):
        return iter([torch.zeros(1)])


def _replacing_loader(calls, broken):
    """First call returns the injected broken module; every later call
    returns a fresh, real one -- so a second acquire after invalidation
    proves the cache reloaded rather than silently reusing `broken`."""
    def load(_path, device):
        calls["n"] += 1
        return broken if calls["n"] == 1 else IFNet(NARROW_NO_ENCODER_BLOCKS, None).eval()
    return load


def test_failed_to_invalidates_the_models_entry_not_reused(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    broken = _PartialPlacementModule()
    models = _lifecycle()
    calls = {"n": 0}
    monkeypatch.setattr(rife_main, "load_ifnet", _replacing_loader(calls, broken))

    with pytest.raises(RuntimeError, match="placement failed partway"):
        rife_main._load_model(path, "cuda", models)

    assert broken.to_calls == ["cuda"]
    assert calls["n"] == 1

    # The broken entry is gone: the next acquire reloads instead of silently
    # reusing a module that may now be split across devices/dtypes.
    second = rife_main._acquire_base_model(models, path)
    assert calls["n"] == 2
    assert second is not broken


def test_failed_half_cast_invalidates_the_models_entry_not_reused(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    broken = _HalfCastFailsModule()
    models = _lifecycle()
    calls = {"n": 0}
    monkeypatch.setattr(rife_main, "load_ifnet", _replacing_loader(calls, broken))

    with pytest.raises(RuntimeError, match="half cast failed partway"):
        rife_main._load_model(path, "cuda", models)

    assert broken.calls == [("to", "cuda"), ("half",)]

    second = rife_main._acquire_base_model(models, path)
    assert calls["n"] == 2
    assert second is not broken


def test_failed_placement_clears_the_fallback_slot_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(rife_main, "_FALLBACK_MODEL", {})
    path = _checkpoint(tmp_path)
    broken = _PartialPlacementModule()
    calls = {"n": 0}
    monkeypatch.setattr(rife_main, "load_ifnet", _replacing_loader(calls, broken))

    with pytest.raises(RuntimeError, match="placement failed partway"):
        rife_main._load_model(path, "cuda", None)

    assert rife_main._FALLBACK_MODEL == {}  # invalidated, not left holding the broken module

    second = rife_main._acquire_base_model(None, path)
    assert calls["n"] == 2
    assert second is not broken


class _EvictTrackingModels:
    """A MODELS double whose `acquire` hands out one pinned model and whose
    `evict_dead_weight` records the key -- proves `process()` invalidates
    through the SAME mechanism other native pipes use to release dead
    weight, not a bespoke one."""

    def __init__(self, model):
        self.model = model
        self.evicted = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        return self.model

    def evict_dead_weight(self, key):
        self.evicted.append(key)
        return True


def test_process_invalidates_the_models_entry_on_failed_placement(tmp_path, monkeypatch):
    _force_cpu(monkeypatch)
    broken = _PartialPlacementModule()
    models = _EvictTrackingModels(broken)
    video = tmp_path / "in.mp4"
    _write_input_video(video)
    checkpoint = _checkpoint(tmp_path)
    monkeypatch.setattr(rife_main, "StreamingMp4Writer", _FakeWriter)
    pipe = RifeInterpolatorPipe({"model": {"file_path": checkpoint}, "factor": 2, "keep_audio": False})

    with pytest.raises(RuntimeError, match="placement failed partway"):
        pipe.process(PipeInput(input={"video": [str(video)], "MODELS": models}), lambda o: None)

    # The original placement error propagated unmasked, AND the cache entry
    # was invalidated through the same evict_dead_weight() mechanism other
    # native pipes use to release dead weight.
    assert models.evicted == [f"native/rife/{checkpoint}"]
