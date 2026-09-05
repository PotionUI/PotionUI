"""matting/birefnet checkpoint lifecycle (MEDIA-03).

`_acquire_model` used to fingerprint on the raw config path string alone
(`fingerprint=model_path`), so `ModelLifecycle.acquire` treated a checkpoint
replaced in place at the same path as unchanged - the stale cached module
was never observed until an unrelated eviction/restart happened to retire
it. `_model_fingerprint` now fingerprints on the checkpoint's on-disk
revision (mtime + size), the same convention `interpolator/rife`'s
`_acquire_base_model` uses, exercised here against a REAL `ModelLifecycle`
rather than a fake standing in for its fingerprint-bust/reuse behaviour.
"""

import gc
import os
import time
import weakref

import pytest

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.matting.birefnet import main as matting_main
from src.pipelines.pipes.matting.birefnet.main import MattingBirefnetPipe
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle


def _checkpoint(tmp_path, name="birefnet.safetensors", content=b"stub-checkpoint"):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def _lifecycle():
    return ModelLifecycle(gpu_monitor=None, settings=None)


class _FakeMatModel:
    """Stand-in for `BackgroundMattingModel`: only the lifecycle's own
    load/reuse/evict bookkeeping is under test here, not inference."""

    def __init__(self):
        self.device_calls = []

    def to(self, device):
        self.device_calls.append(("to", device))
        return self

    def cpu(self):
        self.device_calls.append(("cpu", None))
        return self


def _pipe():
    return MattingBirefnetPipe(MattingBirefnetPipe.get_default_config())


def test_unchanged_reuse_loads_once(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    models = _lifecycle()
    calls = {"n": 0}

    def fake_from_checkpoint(_path):
        calls["n"] += 1
        return _FakeMatModel()

    monkeypatch.setattr(matting_main.BackgroundMattingModel, "from_checkpoint", fake_from_checkpoint)
    pipe = _pipe()
    fingerprint = matting_main._model_fingerprint(path)

    first = pipe._acquire_model(PipeInput(input={"MODELS": models}), path, fingerprint)
    second = pipe._acquire_model(PipeInput(input={"MODELS": models}), path, fingerprint)

    assert calls["n"] == 1
    assert first is second


def test_same_path_revision_replaces_the_obsolete_module(tmp_path, monkeypatch):
    path = _checkpoint(tmp_path)
    models = _lifecycle()
    calls = {"n": 0}

    def fake_from_checkpoint(_path):
        calls["n"] += 1
        return _FakeMatModel()

    monkeypatch.setattr(matting_main.BackgroundMattingModel, "from_checkpoint", fake_from_checkpoint)
    pipe = _pipe()

    fp1 = matting_main._model_fingerprint(path)
    first = pipe._acquire_model(PipeInput(input={"MODELS": models}), path, fp1)
    stale_ref = weakref.ref(first)
    del first  # the cache must be the sole remaining owner for it to be collectible

    # Bump the on-disk revision (mtime + size) without changing the path --
    # e.g. the checkpoint was re-downloaded or swapped in place.
    time.sleep(0.01)
    with open(path, "ab") as f:
        f.write(b"more-bytes")
    os.utime(path, None)
    fp2 = matting_main._model_fingerprint(path)
    assert fp2 != fp1

    second = pipe._acquire_model(PipeInput(input={"MODELS": models}), path, fp2)

    assert calls["n"] == 2
    assert len(models._entries) == 1  # the stale entry was replaced, not appended
    gc.collect()
    assert stale_ref() is None


def test_switching_checkpoints_keeps_a_bounded_retained_count(tmp_path, monkeypatch):
    path_a = _checkpoint(tmp_path, "a.safetensors")
    path_b = _checkpoint(tmp_path, "b.safetensors")
    models = _lifecycle()
    calls = {"a": 0, "b": 0}

    def fake_from_checkpoint(p):
        calls["a" if p == path_a else "b"] += 1
        return _FakeMatModel()

    monkeypatch.setattr(matting_main.BackgroundMattingModel, "from_checkpoint", fake_from_checkpoint)
    pipe = _pipe()

    a1 = pipe._acquire_model(PipeInput(input={"MODELS": models}), path_a, matting_main._model_fingerprint(path_a))
    pipe._acquire_model(PipeInput(input={"MODELS": models}), path_b, matting_main._model_fingerprint(path_b))
    a2 = pipe._acquire_model(PipeInput(input={"MODELS": models}), path_a, matting_main._model_fingerprint(path_a))

    assert a1 is a2  # the switch back to A is a cache hit, not a reload
    assert calls == {"a": 1, "b": 1}
    assert len(models._entries) == 2  # exactly {A, B} -- no third, unbounded entry
