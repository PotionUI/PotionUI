from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

import src.platform.runtime.native.engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator


class _Dit:
    def __init__(self, device: str = "cpu", is_streaming: bool = False):
        self.device = device
        self.is_streaming = is_streaming
        self.offload_calls = 0

    def offload(self) -> None:
        self.offload_calls += 1
        self.device = "cpu"
        self.is_streaming = False


class _Card:
    def __init__(self, free_gb: float, idle_cache_gb: float):
        self.free_gb = free_gb
        self.idle_cache_gb = idle_cache_gb
        self.empty_cache_calls = 0
        self.reads = 0

    def free(self, device) -> float:
        self.reads += 1
        return self.free_gb

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1
        self.free_gb += self.idle_cache_gb
        self.idle_cache_gb = 0.0


def _gen(dit, pipe=None):
    gen = object.__new__(NativeGenerator)
    gen.placement = SimpleNamespace(vae=SimpleNamespace(resident=True))
    gen.spec = SimpleNamespace(latent_format={"latent_channels": 16})
    gen.dit = dit
    gen.vae = SimpleNamespace(module=SimpleNamespace(decode_image=lambda z: z), estimated_vram_gb=0.6, device="cuda:0")
    gen.foreign_calls = []
    gen._ensure_room_for = lambda need, device: gen.foreign_calls.append(need)
    return gen


def _pixels(h: int = 1024, w: int = 1024) -> torch.Tensor:
    return torch.empty((1, 3, h, w), device="meta")


@pytest.fixture
def card(monkeypatch):
    def install(free_gb: float, idle_cache_gb: float) -> _Card:
        state = _Card(free_gb, idle_cache_gb)
        monkeypatch.setattr(engine_mod, "free_vram_gb", state.free)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "empty_cache", state.empty_cache)
        return state

    return install


def test_the_idle_allocator_cache_is_returned_before_the_encode_is_judged(card):
    gen = _gen(_Dit())
    need = gen._encode_need_gb(_pixels())
    state = card(free_gb=1.0, idle_cache_gb=need)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert state.empty_cache_calls == 1
    assert gen._causal3d_encode_tile_for(_pixels(), "cuda:0") is None


def test_a_cache_that_frees_enough_keeps_a_resident_dit_warm(card):
    dit = _Dit(device="cuda:0")
    gen = _gen(dit)
    need = gen._encode_need_gb(_pixels())
    card(free_gb=need - 4.0, idle_cache_gb=5.0)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert dit.offload_calls == 0


def test_the_dit_still_goes_when_the_cache_is_not_enough(card):
    dit = _Dit(device="cuda:0")
    gen = _gen(dit)
    need = gen._encode_need_gb(_pixels())
    state = card(free_gb=need - 4.0, idle_cache_gb=1.0)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert dit.offload_calls == 1
    assert state.empty_cache_calls == 2


def test_no_cache_flush_when_the_encode_already_fits(card):
    gen = _gen(_Dit())
    need = gen._encode_need_gb(_pixels())
    state = card(free_gb=need + 1.0, idle_cache_gb=3.0)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert state.empty_cache_calls == 0
