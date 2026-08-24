"""2D Flux AE decode OOM-retry semantics (no GPU).

Regression coverage for the Klein (Flux2 9B) high-res decode OOM: the caller
decodes with no explicit VRAM figure, so the first attempt must still size 2D
tiling from live free VRAM, and an OOM must retry AFTER the ``except`` block has
exited (the failed attempt's traceback pins its decode activations until then)
using freshly measured post-offload free VRAM — never the stale ``None`` the
caller passed.

CUDA is faked (device is ``cuda:0`` but ``free_vram_gb`` / ``torch.cuda.*`` are
monkeypatched), so no GPU is touched.
"""

from __future__ import annotations

import numpy as np
import torch

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan


class _FakeSpec:
    latent_format: dict = {}
    sampling_settings: dict = {}


class _Fake2DModule:
    """A 2D Flux-AE stand-in: no ``decode_image`` -> the generator's 2D path."""


class _FakeNativeModel:
    def __init__(self, kind, *, gb):
        self.kind = kind
        self.spec = _FakeSpec() if kind == "dit" else None
        self.estimated_vram_gb = gb
        self.compute_dtype = torch.float32
        self.module = _Fake2DModule()
        self.device = "cpu"
        self.offloaded = False

    def move_to(self, device):
        self.device = str(device)

    def offload(self):
        self.offloaded = True
        self.device = "cpu"


def _make_generator():
    dit = _FakeNativeModel("dit", gb=16.9)
    vae = _FakeNativeModel("vae", gb=0.3)
    plan = DevicePlan("cuda:0", "cuda:0", "cuda:0")
    gen = NativeGenerator(dit, te=object(), vae=vae, device_plan=plan)
    return gen, dit, vae


def _fake_cuda(monkeypatch, *, free_gb):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(engine_mod.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(residency, "free_vram_gb", lambda dev: free_gb)
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda dev: free_gb)


def test_first_2d_attempt_sized_from_measured_free_not_caller_none(monkeypatch):
    # The caller passes no vram_free_gb; the first attempt must still receive the
    # measured live free VRAM so 2D tiling can engage instead of a full-res spike.
    _fake_cuda(monkeypatch, free_gb=12.0)
    residency.get_residency_manager().clear()
    gen, _dit, _vae = _make_generator()

    seen: list = []

    def _fake_decode_once(latents, *, vram_free_gb):
        seen.append(vram_free_gb)
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    monkeypatch.setattr(gen, "_decode_once", _fake_decode_once)
    gen.decode(torch.zeros(1, 16, 134, 240))

    assert seen == [12.0]                          # measured free, not None
    residency.get_residency_manager().clear()


def test_2d_decode_oom_retries_tiled_after_offload_with_fresh_free(monkeypatch):
    # First attempt OOMs; the retry must run after the except block, free our DiT,
    # and re-decode sized from freshly measured free VRAM (never the stale None).
    _fake_cuda(monkeypatch, free_gb=19.0)
    residency.get_residency_manager().clear()
    gen, dit, _vae = _make_generator()

    calls: list = []

    def _fake_decode_once(latents, *, vram_free_gb):
        calls.append(vram_free_gb)
        if len(calls) == 1:
            raise torch.cuda.OutOfMemoryError("fake decode OOM")
        # On the retry our DiT must already be offloaded (freed before we allocate).
        assert dit.offloaded is True
        return np.zeros((1, 8, 8, 3), dtype=np.uint8)

    monkeypatch.setattr(gen, "_decode_once", _fake_decode_once)
    out = gen.decode(torch.zeros(1, 16, 134, 240))

    assert len(calls) == 2                          # failed once, retried once
    assert calls[1] == 19.0                         # retry sized from fresh free VRAM
    assert isinstance(out, np.ndarray)
    residency.get_residency_manager().clear()


def test_2d_decode_non_oom_error_is_not_retried(monkeypatch):
    # A non-OOM failure must propagate, not trigger the OOM-retry path.
    _fake_cuda(monkeypatch, free_gb=19.0)
    residency.get_residency_manager().clear()
    gen, _dit, _vae = _make_generator()

    calls: list = []

    def _fake_decode_once(latents, *, vram_free_gb):
        calls.append(vram_free_gb)
        raise RuntimeError("not an OOM")

    monkeypatch.setattr(gen, "_decode_once", _fake_decode_once)
    try:
        gen.decode(torch.zeros(1, 16, 134, 240))
        raised = False
    except RuntimeError:
        raised = True

    assert raised is True
    assert len(calls) == 1                          # no retry on a non-OOM error
    residency.get_residency_manager().clear()
