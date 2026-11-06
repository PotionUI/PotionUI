from __future__ import annotations

import logging
from contextlib import nullcontext

import pytest
import torch

from src.pipelines.pipes._shared.video import wan_experts
from src.pipelines.pipes._shared.video.wan_experts import ExpertRouter
from src.platform.runtime.native.memory.residency import GpuResidencyRegistry
from src.platform.runtime.native.memory.tiering import sampling_headroom_gb

_DEVICE = "cuda:0"
_CONTEXT = {"context": torch.zeros(1, 2, 4)}
_480P_5S = (1, 16, 21, 60, 104)
_480P_1S = (1, 16, 5, 60, 104)
_5B_480P_5S = (1, 48, 21, 30, 52)
_TINY = (1, 16, 5, 30, 30)
_UMT5_GB = 6.27


def _reserve(shape):
    return sampling_headroom_gb((shape[-2], shape[-1]), latent_frames=shape[2])


class _Card:
    def __init__(self, total_gb, pool_gb=0.0, stuck_gb=0.0):
        self.total_gb = total_gb
        self.pool_gb = pool_gb
        self.stuck_gb = stuck_gb
        self.floor_gb = 1.0
        self.registry = GpuResidencyRegistry()
        self.free_reads = []

    def free_gb(self, device):
        self.free_reads.append(self.pool_gb)
        return self.total_gb - self.registry.resident_gb(device) - self.pool_gb - self.stuck_gb

    def effective_free_gb(self, device):
        return self.total_gb - self.registry.resident_gb(device)

    def empty_cache(self):
        self.pool_gb = 0.0


class _TextEncoder:
    def __init__(self, card, size_gb=_UMT5_GB):
        self.card = card
        self.size_gb = size_gb
        self.offloaded = 0
        card.registry.note_resident(self, _DEVICE, size_gb)

    def offload(self):
        self.offloaded += 1
        self.card.pool_gb += self.size_gb
        self.card.registry.note_offloaded(self)


class _Expert:
    def __init__(self, card, est_gb):
        self.card = card
        self.estimated_vram_gb = est_gb
        self.moved = []
        self.streamed = []
        self.offloaded = 0
        self.resident = False
        self.module = lambda x, t, ctx: torch.zeros_like(x)

    def move_to(self, device):
        self.moved.append(device)
        self.resident = True
        self.card.registry.note_resident(self, device, self.estimated_vram_gb)

    def stream_to(self, device, budget):
        self.streamed.append((device, budget))

    def offload(self):
        self.offloaded += 1
        if self.resident:
            self.resident = False
            self.card.pool_gb += self.estimated_vram_gb
        self.card.registry.note_offloaded(self)


@pytest.fixture
def card(monkeypatch):
    def install(total_gb, pool_gb=0.0, stuck_gb=0.0):
        installed = _Card(total_gb, pool_gb, stuck_gb)
        monkeypatch.setattr(wan_experts, "free_vram_gb", installed.free_gb)
        monkeypatch.setattr(wan_experts, "effective_free_vram_gb", installed.effective_free_gb)
        monkeypatch.setattr(wan_experts, "minimum_inference_memory_gb", lambda: installed.floor_gb)
        monkeypatch.setattr(wan_experts, "get_residency_registry", lambda: installed.registry)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "device", lambda device: nullcontext())
        monkeypatch.setattr(torch.cuda, "empty_cache", installed.empty_cache)
        return installed

    return install


def _run(router, sigma):
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([sigma]), _CONTEXT)


def test_the_text_encoder_leaves_when_weights_and_reserve_exceed_free_though_the_weights_alone_fit(card):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)
    free = c.total_gb - _UMT5_GB
    assert expert.estimated_vram_gb < free < expert.estimated_vram_gb + _reserve(_480P_5S)

    _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_480P_5S), 0.95)

    assert encoder.offloaded == 1
    assert c.registry.resident_gb(_DEVICE) == 0.0


def test_the_text_encoder_stays_when_weights_and_reserve_fit(card):
    c = card(60.0)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)

    _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_TINY), 0.95)

    assert encoder.offloaded == 0
    assert expert.moved == [_DEVICE]
    assert c.registry.resident_gb(_DEVICE) == pytest.approx(_UMT5_GB + 2.64)


def test_the_router_never_evicts_its_own_experts(card):
    c = card(24.0)
    encoder = _TextEncoder(c)
    high, low = _Expert(c, 14.0), _Expert(c, 14.0)
    c.registry.note_resident(low, _DEVICE, 14.0)

    _run(ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_480P_5S), 0.95)

    assert encoder.offloaded == 1
    assert low.offloaded == 0
    assert c.registry.resident_gb(_DEVICE) == pytest.approx(14.0)


def test_the_idle_pool_is_handed_back_before_free_is_read(card):
    c = card(23.0, pool_gb=4.0)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 13.31)
    assert c.total_gb - _UMT5_GB - 4.0 < 13.31 + _reserve(_480P_1S) <= c.total_gb - _UMT5_GB

    _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_480P_1S), 0.95)

    assert c.free_reads[0] == 0.0
    assert encoder.offloaded == 0
    assert expert.moved == [_DEVICE]


def test_the_low_expert_is_budgeted_on_the_card_the_resident_high_expert_left(card):
    c = card(31.0)
    high, low = _Expert(c, 13.31), _Expert(c, 13.31)
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_480P_5S)

    _run(router, 0.95)
    _run(router, 0.5)

    assert high.moved == [_DEVICE] and high.offloaded == 1
    assert low.moved == [_DEVICE]
    assert low.streamed == []


def test_the_budget_counts_what_the_eviction_freed(card):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 9.31)
    router = ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_5B_480P_5S)

    budget = router._weights_budget_gb(expert)

    assert encoder.offloaded == 1
    assert budget == pytest.approx(c.total_gb - _reserve(_5B_480P_5S))


def test_what_the_eviction_leaves_in_the_pool_is_handed_back_before_the_expert_lands(card):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 9.31)
    router = ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_5B_480P_5S)

    router._weights_budget_gb(expert)

    assert encoder.offloaded == 1
    assert c.pool_gb == 0.0


def test_an_unreadable_free_returns_the_demand_and_evicts_nothing(card, monkeypatch):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)
    monkeypatch.setattr(wan_experts, "free_vram_gb", lambda device: None)
    router = ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_480P_5S)

    assert router._weights_budget_gb(expert) == pytest.approx(2.64)
    _run(router, 0.95)

    assert encoder.offloaded == 0
    assert expert.moved == [_DEVICE]


def test_the_eviction_is_logged_at_debug_with_what_left_and_for_how_much(card, caplog):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)

    with caplog.at_level(logging.DEBUG, logger=wan_experts.logger.name):
        _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_480P_5S), 0.95)

    assert encoder.offloaded == 1
    lines = [r for r in caplog.records if "evicted" in r.getMessage()]
    assert [r.levelno for r in lines] == [logging.DEBUG]
    assert lines[0].getMessage() == (
        "[GENERATOR WAN] evicted _TextEncoder for 2.64GB of expert weights + "
        f"{_reserve(_480P_5S):.2f}GB reserve ({c.total_gb - _UMT5_GB:.2f}GB free)"
    )


def test_nothing_is_logged_when_nothing_is_evicted(card, caplog):
    c = card(60.0)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)

    with caplog.at_level(logging.DEBUG, logger=wan_experts.logger.name):
        _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_TINY), 0.95)

    assert encoder.offloaded == 0
    assert not [r for r in caplog.records if "evicted" in r.getMessage()]


def test_the_inference_floor_over_a_small_reserve_sends_the_encoder_away(card):
    c = card(10.27)
    c.floor_gb = 2.0
    encoder = _TextEncoder(c)
    expert = _Expert(c, 2.64)
    free = c.total_gb - _UMT5_GB
    assert expert.estimated_vram_gb + _reserve(_TINY) < free < expert.estimated_vram_gb + c.floor_gb

    _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_TINY), 0.95)

    assert encoder.offloaded == 1
    assert expert.moved == [_DEVICE]


def test_the_budget_follows_the_effective_free_memory(card):
    c = card(10.1, stuck_gb=1.5)
    expert = _Expert(c, 9.31)
    router = ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_5B_480P_5S)

    budget = router._weights_budget_gb(expert)

    assert budget == pytest.approx(c.total_gb - _reserve(_5B_480P_5S))


def test_an_expert_of_unknown_size_evicts_nothing(card):
    c = card(10.1)
    encoder = _TextEncoder(c)
    expert = _Expert(c, 0.0)

    _run(ExpertRouter(expert, None, 0.875, _DEVICE, latents_shape=_480P_5S), 0.95)

    assert encoder.offloaded == 0
    assert expert.streamed == [(_DEVICE, 0.0)]
