from __future__ import annotations

import pytest
import torch
import torch.nn as nn

import src.platform.runtime.model_lifecycle.lifecycle as lifecycle
import src.platform.runtime.native.engine as engine_mod
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.memory import partial as partial_mod
from src.platform.runtime.native.memory.partial import (
    ModuleStreamer,
    PinnedMemoryUnavailableError,
    iter_streamable_leaves,
    plan_residency_split,
)
from src.platform.runtime.native.memory.residency import GpuResidencyRegistry
from src.platform.runtime.system_memory import SystemMemory
from src.platform.runtime.native.kernels import disable_weight_init


class _Tiny(nn.Module):
    def __init__(self, n_linear: int = 4, dim: int = 8) -> None:
        super().__init__()
        self.embed = disable_weight_init.Embedding(16, dim)
        self.blocks = nn.ModuleList(disable_weight_init.Linear(dim, dim) for _ in range(n_linear))
        self.norm = disable_weight_init.RMSNorm(dim)
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, tokens: torch.Tensor) -> torch.Tensor:
        x = self.embed(tokens)
        for blk in self.blocks:
            x = blk(x)
        return self.norm(x)


@pytest.fixture
def fake_pinning(monkeypatch):
    pinned: set[int] = set()
    pins: list[int] = []
    alive: list[torch.Tensor] = []

    def _pin(self):
        copy = self.clone()
        alive.append(copy)
        pinned.add(copy.data_ptr())
        pins.append(copy.data_ptr())
        return copy

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
    monkeypatch.setattr(torch.Tensor, "pin_memory", _pin)
    monkeypatch.setattr(torch.Tensor, "is_pinned", lambda self: self.data_ptr() in pinned)
    return pins


def _half_streamed(m: _Tiny):
    fixed = sum(p.numel() * p.element_size() for p in m.embed.parameters())
    two = sum(p.numel() * p.element_size() for blk in m.blocks[:2] for p in blk.parameters())
    return plan_residency_split(m, resident_budget_gb=(fixed + two) / 1024 ** 3)


def test_park_keeps_every_weight_pinned_on_the_cpu_and_reverts_the_streamed_flags(fake_pinning):
    m = _Tiny()
    plan = _half_streamed(m)
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cpu", plan, pin=True)
    streamed = {name: leaf.weight.data.data_ptr() for name, leaf in iter_streamable_leaves(m) if name in plan.streamed_names}

    assert streamer.park() is True

    assert streamer.parked and not streamer.active
    for name, leaf in iter_streamable_leaves(m):
        assert leaf.weight.data.is_pinned()
        assert leaf.comfy_cast_weights == type(leaf).comfy_cast_weights
        if name in streamed:
            assert leaf.weight.data.data_ptr() == streamed[name]
    assert streamer.parked_gb == pytest.approx(sum(p.numel() * p.element_size() for p in m.parameters()) / 1024 ** 3)


def test_a_parked_module_is_placed_again_without_pinning_anything(fake_pinning):
    m = _Tiny()
    tokens = torch.tensor([[1, 2, 3]])
    with torch.no_grad():
        expected = m(tokens)
    plan = _half_streamed(m)
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cpu", plan, pin=True)
    streamer.park()
    fake_pinning.clear()

    streamer.apply("cpu", plan, pin=True)

    assert fake_pinning == []
    assert streamer.active and not streamer.parked
    with torch.no_grad():
        assert torch.equal(m(tokens), expected)


def test_unpark_hands_every_weight_back_to_pageable_memory(fake_pinning):
    m = _Tiny()
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cpu", _half_streamed(m), pin=True)
    streamer.park()

    streamer.unpark()

    assert not streamer.parked
    assert not any(p.data.is_pinned() for p in m.parameters())


def test_park_refuses_an_unpinned_placement():
    m = _Tiny()
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cpu", _half_streamed(m), pin=False)

    assert streamer.park() is False
    assert streamer.active and not streamer.parked


def test_a_pin_failure_while_parking_falls_back_to_a_plain_teardown(fake_pinning, monkeypatch):
    m = _Tiny()
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cpu", _half_streamed(m), pin=True)

    def _refuse(data):
        raise PinnedMemoryUnavailableError("no page-locked memory")

    monkeypatch.setattr(partial_mod, "_pin", _refuse)

    assert streamer.park() is False
    assert not streamer.parked and not streamer.active
    assert not any(p.data.is_pinned() for p in m.parameters())


def _streamed_model(available_gb: float, monkeypatch) -> NativeModel:
    monkeypatch.setattr(engine_mod, "get_system_memory",
                        lambda: SystemMemory(total=64 * 1024 ** 3, available=int(available_gb * 1024 ** 3)))
    monkeypatch.setattr(lifecycle, "empty_pinned_host_cache", lambda: None)
    monkeypatch.setattr(lifecycle, "trim_host_allocator", lambda: None)
    m = _Tiny()
    model = NativeModel("diffusion_model", m, estimated_vram_gb=1.0)
    model._streamer = ModuleStreamer(m, prefetch=False)
    plan = _half_streamed(m)
    model._streamer.apply("cpu", plan, pin=True)
    model.last_stream_plan = plan
    model.device = "cuda:0"
    return model


def test_native_model_parks_when_host_ram_keeps_the_streaming_reserve(fake_pinning, monkeypatch):
    model = _streamed_model(40.0, monkeypatch)

    assert model.park() is True

    assert model.is_parked and model.device == "cpu"


def test_native_model_keeps_the_placement_when_host_ram_would_fall_under_the_reserve(fake_pinning, monkeypatch):
    model = _streamed_model(1.0, monkeypatch)

    assert model.park() is False

    assert not model.is_parked and model._streamer.active


def test_placing_a_parked_model_again_skips_the_host_ram_guard(fake_pinning, monkeypatch):
    model = _streamed_model(40.0, monkeypatch)
    model.park()
    monkeypatch.setattr(model, "_guard_host_ram_for_streaming",
                        lambda plan: pytest.fail("a parked model needs no new pinned memory"))
    monkeypatch.setattr(model._streamer, "apply", lambda device, plan, **kw: None)

    model.stream_to("cuda:0", 0.0)


def test_a_full_move_releases_the_parked_pins(fake_pinning, monkeypatch):
    model = _streamed_model(40.0, monkeypatch)
    model.park()

    model.move_to("cpu")

    assert not model.is_parked
    assert not any(p.data.is_pinned() for p in model.module.parameters())


def test_evicting_a_parked_model_releases_its_pinned_pool(fake_pinning, monkeypatch):
    released = []
    model = _streamed_model(40.0, monkeypatch)
    model.park()
    model.estimated_vram_gb = 13.0
    monkeypatch.setattr(lifecycle, "empty_pinned_host_cache", lambda: released.append(1))
    monkeypatch.setattr(model, "_PINNED_RELEASE_FLOOR_GB", 0.0)

    model.unload()

    assert model.module is None and not model.is_parked
    assert released


def test_offloading_a_parked_model_hands_every_weight_back_to_pageable_memory(fake_pinning, monkeypatch):
    model = _streamed_model(40.0, monkeypatch)
    model.park()

    model.offload()

    assert not model.is_parked and model.device == "cpu"
    assert not any(p.data.is_pinned() for p in model.module.parameters())


def test_a_parked_model_whose_placement_runs_out_of_memory_is_guarded_again_on_the_retry(fake_pinning, monkeypatch):
    model = _streamed_model(40.0, monkeypatch)
    model.park()
    real_move = partial_mod._move_own_tensors

    def _oom_on_resident(module, device, **kwargs):
        if str(device) != "cpu":
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        real_move(module, device, **kwargs)

    monkeypatch.setattr(partial_mod, "_move_own_tensors", _oom_on_resident)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        model.stream_to("cuda:0", 0.0)

    assert not model.is_parked and not model._streamer.active
    assert not any(p.data.is_pinned() for p in model.module.parameters())
    guarded = []
    monkeypatch.setattr(model, "_guard_host_ram_for_streaming", guarded.append)
    monkeypatch.setattr(model._streamer, "apply", lambda device, plan, **kw: None)
    model.stream_to("cuda:0", 0.0)
    assert len(guarded) == 1


def test_native_model_never_parks_a_placement_that_pinned_nothing(fake_pinning, monkeypatch):
    monkeypatch.setattr(engine_mod, "get_system_memory",
                        lambda: SystemMemory(total=64 * 1024 ** 3, available=40 * 1024 ** 3))
    m = _Tiny()
    model = NativeModel("diffusion_model", m, estimated_vram_gb=1.0)
    model._streamer = ModuleStreamer(m, prefetch=False)
    plan = plan_residency_split(m, resident_budget_gb=1.0)
    model._streamer.apply("cpu", plan, pin=True)
    model.last_stream_plan = plan
    assert plan.fully_resident

    assert model.park() is False

    assert not model.is_parked and model._streamer.active


def test_a_parked_model_leaves_the_residency_registry_and_its_derived_caches(fake_pinning, monkeypatch):
    registry = GpuResidencyRegistry()
    monkeypatch.setattr(engine_mod, "get_residency_registry", lambda: registry)
    model = _streamed_model(40.0, monkeypatch)
    registry.note_resident(model, "cuda:0", 1.0)
    released = []
    monkeypatch.setattr(model.module.blocks[0], "release_derived_caches", lambda: released.append(1) or 0, raising=False)

    assert model.park() is True

    assert registry.resident_gb("cuda:0") == 0.0
    assert released


def test_a_full_move_of_a_large_parked_model_releases_the_pinned_pool(fake_pinning, monkeypatch):
    released = []
    model = _streamed_model(40.0, monkeypatch)
    model.park()
    model.estimated_vram_gb = 13.0
    monkeypatch.setattr(lifecycle, "empty_pinned_host_cache", lambda: released.append(1))
    monkeypatch.setattr(model, "_PINNED_RELEASE_FLOOR_GB", 0.0)

    model.move_to("cpu")

    assert released
