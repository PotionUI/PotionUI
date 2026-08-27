"""SDXL <-> native GpuResidencyRegistry registration (cross-engine OOM guard).

A fully-resident SDXL pipe registers an evictable handle with the native engine's
residency manager, so a later native generation can free its VRAM instead of
OOMing on it. These are CPU-only: CUDA availability and full-residency are mocked,
and the diffusers pipe is a stub that records ``.to()`` calls.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.memory.residency import get_residency_registry  # noqa: E402
from src.pipelines.pipes.checkpoint_loader.sdxl import main as sdxl_main  # noqa: E402


class _FakePipe:
    def __init__(self) -> None:
        self.moves: list[str] = []

    def to(self, device):
        self.moves.append(str(device))
        return self


class _FakeModel:
    def __init__(self) -> None:
        self.pipe = _FakePipe()
        self._residency_handle = None


@pytest.fixture(autouse=True)
def _clean_manager(monkeypatch):
    get_residency_registry().clear()
    # Pretend we have a CUDA device so the registration path runs on CPU CI.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    yield
    get_residency_registry().clear()


def _pipe(config=None):
    p = sdxl_main.CheckpointLoaderSDXLPipe.__new__(sdxl_main.CheckpointLoaderSDXLPipe)
    p.config = config or {"device": "cuda"}
    return p


def test_registers_fully_resident_pipe(monkeypatch):
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: True)
    model = _FakeModel()
    _pipe()._register_with_residency(model)

    handle = model._residency_handle
    assert handle is not None and not handle.offloaded
    # It is now tracked as resident on the cuda device.
    assert get_residency_registry()._entries.get(id(handle)) is not None


def test_native_offload_all_evicts_pipe_to_cpu(monkeypatch):
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: True)
    model = _FakeModel()
    _pipe()._register_with_residency(model)
    handle = model._residency_handle

    # A native generation frees foreign residents (nothing of its own excluded).
    evicted = get_residency_registry().offload_all("cuda", exclude=())
    assert handle in evicted
    assert model.pipe.moves[-1] == "cpu"        # pipe moved off the GPU
    assert handle.offloaded is True
    assert get_residency_registry()._entries.get(id(handle)) is None


def test_owned_exclude_never_evicted(monkeypatch):
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: True)
    model = _FakeModel()
    _pipe()._register_with_residency(model)
    handle = model._residency_handle

    evicted = get_residency_registry().offload_all("cuda", exclude=[handle])
    assert handle not in evicted
    assert handle.offloaded is False
    assert get_residency_registry()._entries.get(id(handle)) is not None


def test_reacquire_rehomes_and_reregisters_after_eviction(monkeypatch):
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: True)
    model = _FakeModel()
    pipe = _pipe()
    pipe._register_with_residency(model)
    handle = model._residency_handle

    get_residency_registry().offload_all("cuda", exclude=())
    assert handle.offloaded is True

    # Next SDXL acquire: even though the (mocked) residency check would now be
    # False, the offloaded handle path re-homes the pipe to the GPU and re-registers.
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: False)
    pipe._register_with_residency(model)
    assert model.pipe.moves[-1] == "cuda"
    assert handle.offloaded is False
    assert get_residency_registry()._entries.get(id(handle)) is not None


def test_diffusers_offloaded_pipe_not_registered(monkeypatch):
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: False)
    model = _FakeModel()
    _pipe()._register_with_residency(model)
    assert model._residency_handle is None
    assert get_residency_registry().resident_gb("cuda") == 0.0


def test_noop_without_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(sdxl_main, "_pipe_is_fully_resident", lambda pipe: True)
    model = _FakeModel()
    _pipe()._register_with_residency(model)
    assert model._residency_handle is None
