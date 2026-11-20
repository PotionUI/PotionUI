from __future__ import annotations

import torch

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan


class _Spec:
    latent_format: dict = {}
    sampling_settings: dict = {}


class _Dit:
    kind = "dit"
    spec = _Spec()
    compute_dtype = torch.float32
    device = "cpu"

    def __init__(self, gb):
        self.estimated_vram_gb = gb
        self.module = object()
        self.moved_to: list[str] = []

    def move_to(self, device):
        self.moved_to.append(str(device))
        self.device = str(device)

    def offload(self):
        self.device = "cpu"


class _Vae:
    kind = "vae"
    spec = None
    estimated_vram_gb = 0.6
    module = object()
    device = "cpu"


class _CoResidentTextEncoder:
    def __init__(self):
        self.offloaded = False

    def offload(self):
        self.offloaded = True


class _Ref:
    def __init__(self, h, w):
        self.shape = (1, 16, 1, h, w)


def _generator(monkeypatch, *, free_gb):
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(engine_mod.torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(engine_mod, "free_vram_gb", lambda dev: free_gb)
    monkeypatch.setattr(engine_mod, "minimum_inference_memory_gb", lambda: 1.0)
    registry = residency.get_residency_registry()
    registry.clear()
    te = _CoResidentTextEncoder()
    registry.note_resident(te, "cuda:0", 15.17)
    dit = _Dit(6.76)
    gen = NativeGenerator(dit, te=object(), vae=_Vae(), device_plan=DevicePlan("cuda:0", "cuda:0", "cuda:0"))
    return gen, dit, te, registry


def test_sampling_headroom_counts_the_reference_tokens(monkeypatch):
    gen, *_ = _generator(monkeypatch, free_gb=9.4)
    shape = (1, 16, 1, 134, 240)
    plain = gen._sampling_headroom_gb(shape)
    with_ref = gen._sampling_headroom_gb(shape, [_Ref(134, 240)])
    assert with_ref > plain + 3.0
    residency.get_residency_registry().clear()


def test_resident_dit_placement_evicts_the_co_resident_text_encoder_to_fit_the_activations(monkeypatch):
    gen, dit, te, registry = _generator(monkeypatch, free_gb=9.4)
    shape = (1, 16, 1, 134, 240)
    headroom = gen._sampling_headroom_gb(shape, [_Ref(134, 240)])

    gen._move_dit_to_gpu("cuda:0", headroom_gb=headroom)

    assert te.offloaded is True
    assert dit.moved_to == ["cuda:0"]
    registry.clear()


def test_resident_dit_placement_keeps_the_text_encoder_when_the_activations_fit(monkeypatch):
    gen, dit, te, registry = _generator(monkeypatch, free_gb=20.0)
    shape = (1, 16, 1, 134, 240)
    headroom = gen._sampling_headroom_gb(shape, [_Ref(134, 240)])

    gen._move_dit_to_gpu("cuda:0", headroom_gb=headroom)

    assert te.offloaded is False
    assert dit.moved_to == ["cuda:0"]
    registry.clear()


def test_sample_hands_the_sampling_headroom_to_the_dit_placement(monkeypatch):
    gen, dit, te, registry = _generator(monkeypatch, free_gb=9.4)
    seen: list[float] = []

    def spy(device, headroom_gb=0.0):
        seen.append(headroom_gb)
        raise _Stop

    class _Stop(Exception):
        pass

    monkeypatch.setattr(gen, "_move_dit_to_gpu", spy)
    monkeypatch.setattr(gen, "_build_placement", lambda *a, **k: None)
    cond = {"context": torch.zeros(1, 2, 2), "ref_latents": [_Ref(134, 240)]}
    try:
        gen.sample(cond, (1, 16, 1, 134, 240), steps=1, seed=0, cfg_scale=1.0)
    except _Stop:
        pass
    assert seen and seen[0] > 6.0
    registry.clear()
