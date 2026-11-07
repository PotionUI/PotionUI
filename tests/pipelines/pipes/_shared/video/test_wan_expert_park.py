from __future__ import annotations

import logging
from contextlib import nullcontext
from functools import partial

import pytest
import torch
import torch.nn as nn

import src.platform.runtime.model_lifecycle.lifecycle as lifecycle
import src.platform.runtime.native.engine as engine_mod
from src.pipelines.pipes._shared.video import wan_experts
from src.pipelines.pipes._shared.video.wan_experts import ExpertRouter
from src.platform.runtime import system_memory
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.kernels import disable_weight_init
from src.platform.runtime.native.memory import partial as partial_mod
from src.platform.runtime.native.memory import pinned_arena
from src.platform.runtime.native.memory.residency import GpuResidencyRegistry
from src.platform.runtime.native.memory.tiering import sampling_headroom_gb
from src.platform.runtime.system_memory import SystemMemory
from src.platform.settings import runtime_flags

_GB = 1024 ** 3
_DEVICE = "cuda:0"
_CONTEXT = {"context": torch.zeros(1, 2, 4)}
_SHAPE = (1, 16, 1, 2, 2)


class _Expert:
    def __init__(self, events, tag, est_gb=14.29, parks=True):
        self.events = events
        self.tag = tag
        self.estimated_vram_gb = est_gb
        self.parks = parks
        self.device = "cpu"
        self.is_streaming = False
        self.module = lambda x, t, ctx: torch.zeros_like(x)

    def move_to(self, device):
        self.events.append(f"move {self.tag}")
        self.device = str(device)

    def stream_to(self, device, budget):
        self.events.append(f"stream {self.tag}")
        self.device = str(device)
        self.is_streaming = True

    def park(self):
        self.events.append(f"park {self.tag}")
        if self.parks:
            self.device = "cpu"
            self.is_streaming = False
        return self.parks

    def offload(self):
        self.events.append(f"offload {self.tag}")
        self.device = "cpu"
        self.is_streaming = False


@pytest.fixture
def card(monkeypatch):
    def install(free_gb=10.0, host_ram_gb=62.7, parking=True):
        registry = GpuResidencyRegistry()
        monkeypatch.setattr(wan_experts, "free_vram_gb", lambda device: free_gb)
        monkeypatch.setattr(wan_experts, "effective_free_vram_gb", lambda device: free_gb)
        monkeypatch.setattr(wan_experts, "minimum_inference_memory_gb", lambda: 1.0)
        monkeypatch.setattr(wan_experts, "get_residency_registry", lambda: registry)
        monkeypatch.setattr(wan_experts, "get_system_memory",
                            lambda: SystemMemory(total=int(host_ram_gb * _GB), available=int(host_ram_gb * _GB / 2)))
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        monkeypatch.setattr(torch.cuda, "device", lambda device: nullcontext())
        monkeypatch.setattr(torch.cuda, "empty_cache", lambda: None)
        monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_park_experts", parking)
        return registry

    return install


def _switch(high, low, caplog):
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)
    high.events.clear()
    with caplog.at_level(logging.DEBUG, logger=wan_experts.logger.name):
        router(torch.zeros(_SHAPE), torch.tensor([0.5]), _CONTEXT)
    return [r for r in caplog.records if "parked pinned" in r.getMessage() or "torn down" in r.getMessage()]


def test_the_streamed_high_expert_is_parked_at_the_switch_instead_of_torn_down(card, caplog):
    card()
    events = []

    lines = _switch(_Expert(events, "high"), _Expert(events, "low"), caplog)

    assert events == ["park high", "stream low"]
    assert [r.levelno for r in lines] == [logging.DEBUG]
    assert lines[0].getMessage() == (
        "[GENERATOR WAN] high expert parked pinned: up to 28.58GB pinned with the next expert, limit 37.62GB"
    )


def test_an_expert_that_cannot_be_parked_is_torn_down(card, caplog):
    card()
    events = []

    lines = _switch(_Expert(events, "high", parks=False), _Expert(events, "low"), caplog)

    assert events == ["park high", "offload high", "stream low"]
    assert lines[0].getMessage() == (
        "[GENERATOR WAN] high expert torn down: its placement is not pinned or free host RAM would fall under "
        "the streaming reserve"
    )


def test_no_expert_is_parked_when_both_would_pin_over_sixty_percent_of_host_ram(card, caplog):
    card(host_ram_gb=46.0)
    events = []

    lines = _switch(_Expert(events, "high"), _Expert(events, "low"), caplog)

    assert events == ["offload high", "stream low"]
    assert lines[0].getMessage() == (
        "[GENERATOR WAN] high expert torn down: up to 28.58GB pinned with the next expert, over 27.60GB "
        "(60% of total RAM)"
    )


def test_an_expert_is_parked_when_both_pin_exactly_sixty_percent_of_host_ram(card, caplog):
    card(host_ram_gb=50.0)
    events = []

    _switch(_Expert(events, "high", est_gb=15.0), _Expert(events, "low", est_gb=15.0), caplog)

    assert events == ["park high", "stream low"]


def test_inside_a_memory_limited_cgroup_the_share_is_taken_of_the_cgroup_limit(card, caplog, monkeypatch, tmp_path):
    card(free_gb=0.2)
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "memory.max").write_text(str(_GB))
    (root / "memory.current").write_text("0")
    (root / "memory.stat").write_text("inactive_file 0\n")
    proc = tmp_path / "proc-self-cgroup"
    proc.write_text("0::/\n")
    system_memory.reset_warning_for_tests()
    monkeypatch.setattr(wan_experts, "get_system_memory",
                        partial(system_memory.get_system_memory, cgroup_root=root, proc_cgroup_path=proc))
    events = []

    lines = _switch(_Expert(events, "high", est_gb=0.4), _Expert(events, "low", est_gb=0.4), caplog)
    system_memory.reset_warning_for_tests()

    assert events == ["offload high", "stream low"]
    assert lines[0].getMessage().endswith("over 0.60GB (60% of total RAM)")


def test_the_lora_deltas_of_both_experts_count_toward_the_limit(card, caplog, monkeypatch):
    card(host_ram_gb=50.0)
    monkeypatch.setattr(wan_experts, "_dit_lora_delta_gb", lambda dit: 1.0)
    events = []

    lines = _switch(_Expert(events, "high"), _Expert(events, "low"), caplog)

    assert events == ["offload high", "stream low"]
    assert lines[0].getMessage().startswith("[GENERATOR WAN] high expert torn down: up to 30.58GB pinned")


def test_parking_is_a_live_memory_setting_of_the_backend_on_by_default():
    flag = runtime_flags.RUNTIME_FLAG_BY_KEY["native_park_experts"]

    assert (flag.default, flag.kind, flag.applies, flag.group, flag.scope, flag.env_var) == (True, "bool", "live", "memory", "backend", None)


def test_no_expert_is_parked_when_parking_is_off(card, caplog):
    card(parking=False)
    events = []

    lines = _switch(_Expert(events, "high"), _Expert(events, "low"), caplog)

    assert events == ["offload high", "stream low"]
    assert lines[0].getMessage() == "[GENERATOR WAN] high expert torn down: parking is off (native_park_experts)"


def test_a_resident_expert_is_torn_down_as_before(card, caplog):
    card(free_gb=30.0)
    events = []

    lines = _switch(_Expert(events, "high", est_gb=2.0), _Expert(events, "low", est_gb=2.0), caplog)

    assert events == ["offload high", "move low"]
    assert lines[0].getMessage() == "[GENERATOR WAN] high expert torn down: it was not streamed from pinned RAM"


def test_an_expert_already_off_the_card_is_offloaded_without_a_park_attempt(card, caplog):
    card()
    events = []
    high, low = _Expert(events, "high"), _Expert(events, "low")
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.5]), _CONTEXT)
    router.active.offload()
    events.clear()

    with caplog.at_level(logging.DEBUG, logger=wan_experts.logger.name):
        router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)

    assert events == ["offload low", "stream high"]
    assert not [r for r in caplog.records if "parked pinned" in r.getMessage() or "torn down" in r.getMessage()]


class _Tiny(nn.Module):
    def __init__(self, n_linear: int = 4, dim: int = 32) -> None:
        super().__init__()
        self.embed = disable_weight_init.Embedding(16, dim)
        self.blocks = nn.ModuleList(disable_weight_init.Linear(dim, dim) for _ in range(n_linear))
        self.norm = disable_weight_init.RMSNorm(dim)
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, x, t, ctx):
        return torch.zeros_like(x)


class _FakeCudaHost:
    def __init__(self, monkeypatch) -> None:
        self.registered: dict[int, int] = {}
        self.registers = 0
        monkeypatch.setattr(pinned_arena, "_host_register", self._register)
        monkeypatch.setattr(pinned_arena, "_host_unregister", lambda ptr: self.registered.pop(ptr))
        monkeypatch.setattr(pinned_arena, "_synchronize", lambda device: None)
        monkeypatch.setattr(pinned_arena, "add_pinned_bytes", lambda n: None)

    def _register(self, ptr: int, nbytes: int) -> None:
        self.registers += 1
        self.registered[ptr] = nbytes


@pytest.fixture
def host(monkeypatch, card):
    def _move(module, device, **kwargs):
        if str(device) == "cpu":
            return real_move(module, device, **kwargs)
        for p in module.parameters(recurse=False):
            p.data = p.data.clone()

    real_move = partial_mod._move_own_tensors
    monkeypatch.setattr(partial_mod, "_move_own_tensors", _move)
    monkeypatch.setattr(partial_mod, "_release_idle_vram", lambda: None)
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: pytest.fail("per-tensor pin_memory used"))
    monkeypatch.setattr(engine_mod, "get_system_memory", lambda: SystemMemory(total=64 * _GB, available=40 * _GB))
    monkeypatch.setattr(lifecycle, "empty_pinned_host_cache", lambda: None)
    monkeypatch.setattr(lifecycle, "trim_host_allocator", lambda: None)
    registry = card(free_gb=_two_leaves_free_gb())
    monkeypatch.setattr(engine_mod, "get_residency_registry", lambda: registry)
    return _FakeCudaHost(monkeypatch)


def _two_leaves_free_gb() -> float:
    leaf_bytes = 32 * 32 * 4 + 32 * 4
    fixed_bytes = 16 * 32 * 4
    reserve = sampling_headroom_gb((_SHAPE[-2], _SHAPE[-1]), latent_frames=_SHAPE[2])
    return reserve + (fixed_bytes + 2 * leaf_bytes + 64) / _GB


def _experts():
    return [NativeModel("diffusion_model", _Tiny(), estimated_vram_gb=14.29) for _ in range(2)]


def _generate(high, low):
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)
    router(torch.zeros(_SHAPE), torch.tensor([0.5]), _CONTEXT)
    router.active.offload()


def test_the_next_generation_places_the_parked_high_expert_again_without_pinning_anything(host, monkeypatch):
    high, low = _experts()
    _generate(high, low)
    assert high.is_parked and not low.is_parked
    monkeypatch.setattr(high, "_guard_host_ram_for_streaming",
                        lambda plan: pytest.fail("a parked expert needs no new pinned memory"))
    registers = host.registers
    parked_segments = set(host.registered)

    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)

    assert high.is_streaming and not high.is_parked
    assert host.registers == registers
    assert set(host.registered) == parked_segments


def test_later_generations_park_and_place_the_high_expert_in_the_memory_it_already_holds(host):
    high, low = _experts()
    _generate(high, low)
    parked_segments = set(host.registered)
    before = host.registers
    _generate(high, low)
    second = host.registers - before

    _generate(high, low)

    assert high.is_parked and not low.is_parked
    assert set(host.registered) == parked_segments
    assert second > 0 and host.registers - before == 2 * second


def test_parking_the_high_expert_again_needs_free_ram_only_for_the_streaming_reserve(host, monkeypatch):
    high, low = _experts()
    _generate(high, low)
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)
    resident_gb = high.last_stream_plan.resident_gb
    assert high._streamer.held_gb == pytest.approx(resident_gb)
    low.stream_to(_DEVICE, resident_gb)
    assert low.last_stream_plan.resident_gb == pytest.approx(resident_gb)
    tight = SystemMemory(total=64 * _GB, available=int((NativeModel._STREAM_HOST_RESERVE_GB + resident_gb / 2) * _GB))
    monkeypatch.setattr(engine_mod, "get_system_memory", lambda: tight)

    assert low.park() is False
    assert high.park() is True


def test_a_parked_high_expert_whose_placement_runs_out_of_memory_streams_in_full(host, monkeypatch):
    high, low = _experts()
    _generate(high, low)
    budgets = []
    real_stream_to = high.stream_to

    def _stream_to(device, budget, **kwargs):
        budgets.append(budget)
        if len(budgets) == 1:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        real_stream_to(device, budget, **kwargs)

    monkeypatch.setattr(high, "stream_to", _stream_to)

    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)
    router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)

    assert budgets[1] == 0.0 and len(budgets) == 2
    assert high.is_streaming and not high.is_parked


def test_an_error_that_is_not_an_oom_propagates_from_a_parked_high_expert(host, monkeypatch):
    high, low = _experts()
    _generate(high, low)

    def _stream_to(device, budget, **kwargs):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")

    monkeypatch.setattr(high, "stream_to", _stream_to)
    router = ExpertRouter(high, low, 0.875, _DEVICE, latents_shape=_SHAPE)

    with pytest.raises(RuntimeError, match="illegal memory access"):
        router(torch.zeros(_SHAPE), torch.tensor([0.95]), _CONTEXT)
    assert high.is_parked
