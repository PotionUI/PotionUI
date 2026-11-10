from __future__ import annotations

import logging
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

    def effective(self, device) -> float:
        return self.free_gb + self.idle_cache_gb

    def empty_cache(self) -> None:
        self.empty_cache_calls += 1
        self.free_gb += self.idle_cache_gb
        self.idle_cache_gb = 0.0


class _Registry:
    def __init__(self):
        self.seen_free = []
        self.evicted = []

    def ensure_free(self, device, need_gb, current_free_gb, *, exclude=()):
        self.seen_free.append(current_free_gb)
        if current_free_gb >= need_gb:
            return []
        self.evicted.append("foreign")
        return ["foreign"]

    def offload_all(self, device, *, exclude=()):
        self.evicted.append("all")
        return ["foreign"]


def _gen_with_registry(dit, monkeypatch):
    gen = _gen(dit)
    del gen._ensure_room_for
    registry = _Registry()
    monkeypatch.setattr(engine_mod, "get_residency_registry", lambda: registry)
    return gen, registry


def _gen(dit, pipe=None):
    gen = object.__new__(NativeGenerator)
    gen.placement = SimpleNamespace(vae=SimpleNamespace(resident=True))
    gen.spec = SimpleNamespace(latent_format={"latent_channels": 16})
    gen.dit = dit
    gen.vae = SimpleNamespace(module=SimpleNamespace(decode_image=lambda z: z), estimated_vram_gb=0.6, device="cuda:0")
    gen.foreign_calls = []
    gen._ensure_room_for = lambda need, device: gen.foreign_calls.append(need)
    return gen


@pytest.fixture(autouse=True)
def _cuda_with_cudnn(monkeypatch):
    monkeypatch.setattr(torch.version, "cuda", "13.0", raising=False)
    monkeypatch.setattr(torch.version, "hip", None, raising=False)
    monkeypatch.setattr(torch.backends.cudnn, "is_available", lambda: True)
    monkeypatch.setattr(torch.backends.cudnn, "enabled", True)


def _pixels(h: int = 1024, w: int = 1024) -> torch.Tensor:
    return torch.empty((1, 3, h, w), device="meta")


@pytest.fixture
def card(monkeypatch):
    def install(free_gb: float, idle_cache_gb: float) -> _Card:
        state = _Card(free_gb, idle_cache_gb)
        monkeypatch.setattr(engine_mod, "free_vram_gb", state.free)
        monkeypatch.setattr(engine_mod, "effective_free_vram_gb", state.effective, raising=False)
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


def test_an_idle_pool_that_covers_the_encode_evicts_no_foreign_resident_and_keeps_the_dit(card, monkeypatch):
    dit = _Dit(device="cuda:0")
    gen, registry = _gen_with_registry(dit, monkeypatch)
    state = card(free_gb=1.0, idle_cache_gb=9.27)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert registry.evicted == []
    assert dit.offload_calls == 0
    assert dit.device == "cuda:0"
    assert state.empty_cache_calls == 1
    assert gen._causal3d_encode_tile_for(_pixels(), "cuda:0") is None


def test_foreign_eviction_is_judged_on_the_room_left_after_the_pool_is_returned(card, monkeypatch):
    gen, registry = _gen_with_registry(_Dit(), monkeypatch)
    state = card(free_gb=1.0, idle_cache_gb=3.0)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert registry.seen_free == [pytest.approx(4.0)]
    assert registry.evicted == ["foreign"]
    assert state.empty_cache_calls == 2


def test_no_flush_when_the_allocator_holds_no_idle_pool(card, monkeypatch):
    gen, registry = _gen_with_registry(_Dit(), monkeypatch)
    state = card(free_gb=1.0, idle_cache_gb=0.0)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert registry.seen_free == [pytest.approx(1.0)]
    assert state.empty_cache_calls == 1


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


def test_a_qwen21_edit_left_with_one_gb_free_and_a_full_cache_encodes_whole(card):
    gen = _gen(_Dit())
    state = card(free_gb=1.0, idle_cache_gb=8.6)
    gen._ensure_room_for_encode(_pixels(), "cuda:0")
    assert state.free_gb == pytest.approx(9.6)
    assert gen._causal3d_encode_tile_for(_pixels(), "cuda:0") is None


@pytest.mark.parametrize(
    ("h", "w", "peak_reserved_gb"),
    [(1024, 1024, 3.03), (928, 1664, 4.46), (1072, 1920, 5.96), (768, 1360, 3.03)],
)
def test_the_estimate_keeps_a_margin_over_the_measured_whole_encode(h, w, peak_reserved_gb):
    gen = _gen(_Dit())
    need = gen._encode_need_gb(_pixels(h, w))
    assert need >= 1.5 * peak_reserved_gb


@pytest.mark.parametrize(("h", "w"), [(1024, 1024), (928, 1664), (768, 1360)])
def test_reference_images_of_the_qwen_presets_fit_a_cleared_12gb_card_whole(h, w):
    gen = _gen(_Dit())
    assert gen._encode_need_gb(_pixels(h, w)) <= 9.6


def test_krea2_enhance_2048_still_tiles_on_a_cleared_12gb_card(card):
    gen = _gen(_Dit())
    card(free_gb=10.6, idle_cache_gb=0.0)
    tile = gen._causal3d_encode_tile_for(_pixels(2048, 2048), "cuda:0")
    assert tile is not None
    assert tile[0] < 2048


def test_cuda_with_cudnn_uses_the_measured_coefficient():
    assert engine_mod._causal3d_encode_mb_per_latent_px() == 0.25


@pytest.mark.parametrize("path", ["cudnn_disabled", "cudnn_missing", "rocm", "no_cuda"])
def test_every_unmeasured_conv_path_falls_back_to_the_old_coefficient(monkeypatch, path):
    if path == "cudnn_disabled":
        monkeypatch.setattr(torch.backends.cudnn, "enabled", False)
    elif path == "cudnn_missing":
        monkeypatch.setattr(torch.backends.cudnn, "is_available", lambda: False)
    elif path == "rocm":
        monkeypatch.setattr(torch.version, "hip", "6.4", raising=False)
    else:
        monkeypatch.setattr(torch.version, "cuda", None, raising=False)
    assert engine_mod._causal3d_encode_mb_per_latent_px() == 0.5


def test_room_tile_decision_and_tile_size_read_the_coefficient_at_call_time(card, monkeypatch, caplog):
    gen, registry = _gen_with_registry(_Dit(), monkeypatch)
    card(free_gb=8.0, idle_cache_gb=0.0)
    pixels = _pixels()
    assert gen._encode_need_gb(pixels) == pytest.approx(6.0)
    gen._ensure_room_for_encode(pixels, "cuda:0")
    assert registry.seen_free == []
    assert gen._causal3d_encode_tile_for(pixels, "cuda:0") is None
    measured_tile = engine_mod._causal3d_encode_tile(10.0, (2048, 2048), 8)
    monkeypatch.setattr(torch.backends.cudnn, "enabled", False)
    assert gen._encode_need_gb(pixels) == pytest.approx(10.0)
    gen._ensure_room_for_encode(pixels, "cuda:0")
    assert registry.seen_free == [pytest.approx(8.0)]
    with caplog.at_level(logging.INFO, logger=engine_mod.logger.name):
        assert gen._causal3d_encode_tile_for(pixels, "cuda:0") is not None
    assert "needs ~10.0GB" in caplog.text
    assert engine_mod._causal3d_encode_tile(10.0, (2048, 2048), 8) == (880, 104)
    assert measured_tile == (1024, 128)


@pytest.mark.parametrize(
    ("h", "w", "peak_reserved_gb"),
    [(512, 512, 1.74), (768, 768, 3.83), (1024, 1024, 6.80)],
)
def test_without_cudnn_the_estimate_keeps_a_margin_over_the_measured_vol2col_encode(monkeypatch, h, w, peak_reserved_gb):
    monkeypatch.setattr(torch.backends.cudnn, "enabled", False)
    gen = _gen(_Dit())
    assert gen._encode_need_gb(_pixels(h, w)) >= 1.4 * peak_reserved_gb
