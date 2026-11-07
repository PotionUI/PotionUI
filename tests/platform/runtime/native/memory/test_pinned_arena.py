from __future__ import annotations

import gc
import logging

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.memory import partial, pinned_arena
from src.platform.runtime.native.memory.partial import ModuleStreamer, iter_streamable_leaves, plan_residency_split
from src.platform.runtime.native.memory.pinned_arena import (
    ARENA_ALIGNMENT,
    PinnedArena,
    aligned_bytes,
    caching_host_block_bytes,
    plan_segments,
)
from src.platform.runtime.native.kernels import disable_weight_init

_GB = 1024 ** 3


class _FakeCudaHost:
    def __init__(
        self, monkeypatch, *, fail_on_call: int | None = None, fail_sync: bool = False,
        fail_unregister: bool = False,
    ) -> None:
        self.events: list[tuple] = []
        self.registered: dict[int, int] = {}
        self.unregister_attempts: list[int] = []
        self._fail_on_call = fail_on_call
        self._fail_sync = fail_sync
        self._fail_unregister = fail_unregister
        self._calls = 0
        monkeypatch.setattr(pinned_arena, "_host_register", self._register)
        monkeypatch.setattr(pinned_arena, "_host_unregister", self._unregister)
        monkeypatch.setattr(pinned_arena, "_synchronize", self._synchronize)
        monkeypatch.setattr(pinned_arena, "add_pinned_bytes", lambda n: self.events.append(("account", n)))

    def _synchronize(self, device) -> None:
        self.events.append(("sync", device))
        if self._fail_sync:
            raise RuntimeError("cudaErrorLaunchFailure")

    def _register(self, ptr: int, nbytes: int) -> None:
        self._calls += 1
        if self._fail_on_call is not None and self._calls == self._fail_on_call:
            raise RuntimeError("cudaErrorHostMemoryAlreadyRegistered")
        assert ptr not in self.registered
        self.registered[ptr] = nbytes
        self.events.append(("register", ptr, nbytes))

    def _unregister(self, ptr: int) -> None:
        self.unregister_attempts.append(ptr)
        if self._fail_unregister:
            raise RuntimeError("cudaErrorInvalidValue")
        assert ptr in self.registered, "unregistered a pointer that was never registered"
        del self.registered[ptr]
        self.events.append(("unregister", ptr))

    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]

    def unregistered(self) -> list[int]:
        return [event[1] for event in self.events if event[0] == "unregister"]


def _params(*tensors: torch.Tensor) -> list[nn.Parameter]:
    return [nn.Parameter(t, requires_grad=False) for t in tensors]


def _mixed_params() -> list[nn.Parameter]:
    return _params(
        torch.randn(7, 5),
        torch.randn(3, dtype=torch.bfloat16),
        torch.randn(4, 9).to(torch.float8_e4m3fn),
        torch.randint(-128, 127, (6, 11), dtype=torch.int8),
        torch.tensor(1.5),
    )


def _arena_bytes(params: list[nn.Parameter]) -> int:
    return sum(aligned_bytes(p.numel() * p.element_size()) for p in params)


class _Tiny(nn.Module):
    def __init__(self, n_linear: int = 4, dim: int = 6) -> None:
        super().__init__()
        self.embed = disable_weight_init.Embedding(16, dim)
        self.blocks = nn.ModuleList(disable_weight_init.Linear(dim, dim) for _ in range(n_linear))
        self.norm = disable_weight_init.RMSNorm(dim)
        for p in self.parameters():
            nn.init.normal_(p)


def _streamed_params(m: nn.Module) -> list[nn.Parameter]:
    return [p for _, leaf in iter_streamable_leaves(m) for p in leaf.parameters(recurse=False)]


def _fake_cuda_streamer(monkeypatch, **fake_kwargs) -> _FakeCudaHost:
    monkeypatch.setattr(partial.torch.cuda, "is_available", lambda: True)
    return _FakeCudaHost(monkeypatch, **fake_kwargs)


def test_caching_host_block_bytes_matches_the_power_of_two_rounding():
    assert [caching_host_block_bytes(n) for n in (0, 1, 2, 3, 4096, 4097)] == [0, 1, 2, 4, 4096, 8192]
    h3_out_proj_fp8 = 7168 * 5376
    assert caching_host_block_bytes(h3_out_proj_fp8) == 1 << 26


def test_plan_segments_aligns_every_offset_and_splits_at_the_cap():
    segments = plan_segments([10, 5000, 4096, 1, 20000], segment_bytes=8192, alignment=4096)
    assert segments == [
        (4096, [(0, 0)]),
        (8192, [(1, 0)]),
        (8192, [(2, 0), (3, 4096)]),
        (20480, [(4, 0)]),
    ]


def test_pin_carves_aligned_views_that_keep_every_value(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _mixed_params()
    originals = [p.data.clone() for p in params]
    arena = PinnedArena(segment_bytes=_GB)
    arena.pin(params)

    [(ptr, nbytes)] = fake.registered.items()
    assert ptr % ARENA_ALIGNMENT == 0
    assert nbytes == _arena_bytes(params)
    for param, original in zip(params, originals):
        assert param.data.data_ptr() % ARENA_ALIGNMENT == 0
        assert ptr <= param.data.data_ptr() < ptr + nbytes
        assert arena.owns(param.data)
        assert param.data.dtype == original.dtype and param.data.shape == original.shape
        assert torch.equal(param.data.reshape(-1).view(torch.uint8), original.reshape(-1).view(torch.uint8))
    arena.release()


def test_accounting_reports_registered_bytes_not_the_rounded_request(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _params(torch.empty(7168, 3, dtype=torch.uint8), torch.empty(5000, dtype=torch.uint8))
    arena = PinnedArena(segment_bytes=_GB)
    arena.pin(params)
    accounted = [event[1] for event in fake.events if event[0] == "account"]
    assert accounted == [_arena_bytes(params)] == [arena.pinned_bytes]
    assert arena.pinned_bytes < sum(caching_host_block_bytes(p.numel()) for p in params)
    arena.release()
    assert arena.pinned_bytes == 0


def test_unpin_releases_a_segment_once_its_last_view_leaves(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _params(*(torch.randn(1024) for _ in range(4)))
    originals = [p.data.clone() for p in params]
    arena = PinnedArena(segment_bytes=2 * ARENA_ALIGNMENT, device="cuda:1")
    arena.pin(params)
    first, second = list(fake.registered)
    assert arena.registered_segments == 2

    assert arena.unpin(params[0])
    assert fake.unregistered() == []
    assert arena.unpin(params[1])
    assert fake.unregistered() == [first]
    assert fake.kinds()[-2:] == ["sync", "unregister"]
    assert fake.events[-2] == ("sync", torch.device("cuda:1"))
    assert arena.registered_segments == 1 and arena.owns(params[2].data)

    for param, original in zip(params[:2], originals):
        assert not arena.owns(param.data)
        assert torch.equal(param.data, original)
    assert not arena.unpin(params[0])
    arena.release()
    assert fake.unregistered() == [first, second]


def test_release_unregisters_each_segment_once_after_a_device_sync(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _params(*(torch.randn(1024) for _ in range(3)))
    arena = PinnedArena(segment_bytes=ARENA_ALIGNMENT)
    arena.pin(params)
    ptrs = list(fake.registered)
    arena.release()
    arena.release()
    assert sorted(fake.unregistered()) == sorted(ptrs)
    assert fake.kinds()[-4:] == ["sync", "unregister", "unregister", "unregister"]
    assert fake.registered == {}
    assert not arena.owns(params[0].data)
    assert all(torch.isfinite(p.data).all() for p in params)


def test_a_failed_device_sync_still_unregisters_every_segment_once(monkeypatch, caplog):
    fake = _FakeCudaHost(monkeypatch, fail_sync=True)
    params = _params(*(torch.randn(1024) for _ in range(3)))
    arena = PinnedArena(segment_bytes=ARENA_ALIGNMENT)
    arena.pin(params)
    ptrs = list(fake.registered)
    with caplog.at_level(logging.WARNING, logger=pinned_arena.__name__):
        arena.release()
        arena.release()
    assert fake.registered == {}
    assert sorted(fake.unregister_attempts) == sorted(ptrs)
    assert "device sync before unregister failed" in caplog.text


def test_a_failed_unregister_is_logged_and_never_retried(monkeypatch, caplog):
    fake = _FakeCudaHost(monkeypatch, fail_unregister=True)
    params = _params(*(torch.randn(1024) for _ in range(3)))
    arena = PinnedArena(segment_bytes=ARENA_ALIGNMENT)
    arena.pin(params)
    ptrs = list(fake.registered)
    with caplog.at_level(logging.WARNING, logger=pinned_arena.__name__):
        assert arena.unpin(params[0])
        assert len(fake.unregister_attempts) == 1
        arena.release()
        arena.release()
        del arena
        gc.collect()
    assert sorted(fake.unregister_attempts) == sorted(ptrs)
    assert caplog.text.count("cudaHostUnregister failed") == len(ptrs)
    assert all(torch.isfinite(p.data).all() for p in params)


def test_an_arena_dropped_without_release_still_unregisters(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _params(*(torch.randn(1024) for _ in range(2)))
    arena = PinnedArena(segment_bytes=ARENA_ALIGNMENT)
    arena.pin(params)
    assert len(fake.registered) == 2
    del arena
    gc.collect()
    assert fake.registered == {}


def test_a_failed_registration_leaves_nothing_registered_after_release(monkeypatch):
    fake = _FakeCudaHost(monkeypatch, fail_on_call=2)
    params = _params(*(torch.randn(1024) for _ in range(3)))
    originals = [p.data.clone() for p in params]
    arena = PinnedArena(segment_bytes=ARENA_ALIGNMENT)
    with pytest.raises(RuntimeError):
        arena.pin(params)
    assert len(fake.registered) == 1
    arena.release()
    assert fake.registered == {}
    for param, original in zip(params, originals):
        assert torch.equal(param.data, original)


def test_release_unused_drops_only_segments_whose_views_moved_away(monkeypatch):
    fake = _FakeCudaHost(monkeypatch)
    params = _params(*(torch.randn(1024) for _ in range(4)))
    arena = PinnedArena(segment_bytes=2 * ARENA_ALIGNMENT)
    arena.pin(params)
    first, second = list(fake.registered)
    params[0].data = torch.zeros(1024)
    arena.release_unused()
    assert fake.unregistered() == []
    params[1].data = torch.zeros(1024)
    arena.release_unused()
    assert fake.unregistered() == [first]
    assert arena.owns(params[3].data)
    arena.release()
    assert fake.unregistered() == [first, second]


def test_streamer_pins_every_streamed_weight_into_the_arena(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: pytest.fail("per-tensor pin_memory used"))
    m = _Tiny()
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)

    streamed = _streamed_params(m)
    [arena] = streamer._arenas
    assert all(arena.owns(p.data) for p in streamed)
    assert not arena.owns(m.embed.weight.data)
    assert len(fake.registered) == 1
    assert streamer.pinned_gb * _GB == _arena_bytes(streamed) == arena.pinned_bytes
    streamer.teardown()


def test_streamer_teardown_copies_weights_out_and_unregisters(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m = _Tiny()
    before = {name: p.data.clone() for name, p in m.named_parameters()}
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)
    ranges = list(fake.registered.items())
    streamer.teardown()

    assert fake.registered == {}
    assert streamer._arenas == []
    for name, p in m.named_parameters():
        assert not any(ptr <= p.data.data_ptr() < ptr + n for ptr, n in ranges)
        assert torch.equal(p.data, before[name])
    assert all(leaf.comfy_cast_weights is False for _, leaf in iter_streamable_leaves(m))


def test_streamer_teardown_frees_each_segment_before_the_sweep_ends(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m = _Tiny(n_linear=6, dim=32)
    leaf_bytes = 2 * aligned_bytes(32 * 32 * 4)
    monkeypatch.setattr(partial, "_TEARDOWN_RELEASE_CHUNK_GB", leaf_bytes / _GB)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)
    arena = streamer._arenas[0]
    assert arena.registered_segments >= 6

    still_pinned_at_release: list[int] = []
    unregister = pinned_arena._host_unregister
    monkeypatch.setattr(
        pinned_arena, "_host_unregister",
        lambda ptr: (still_pinned_at_release.append(sum(arena.owns(p.data) for p in _streamed_params(m))),
                     unregister(ptr)),
    )
    streamer.teardown()
    assert fake.registered == {}
    assert still_pinned_at_release[0] >= len(_streamed_params(m)) - 2
    assert still_pinned_at_release == sorted(still_pinned_at_release, reverse=True)


def test_streamer_falls_back_to_per_tensor_pins_with_rounded_accounting(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch, fail_on_call=2)
    monkeypatch.setattr(partial, "_TEARDOWN_RELEASE_CHUNK_GB", ARENA_ALIGNMENT / _GB)
    pinned: list[int] = []
    accounted: list[int] = []
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: pinned.append(self.numel()) or self.clone())
    monkeypatch.setattr(partial, "add_pinned_bytes", accounted.append)
    m = _Tiny(dim=6)
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)

    streamed = _streamed_params(m)
    assert streamer._arenas == [] and fake.registered == {}
    assert fake.unregistered() and len(pinned) == len(streamed)
    assert sorted(accounted) == sorted(caching_host_block_bytes(p.numel() * p.element_size()) for p in streamed)
    assert 6 * 6 * 4 not in accounted
    streamer.teardown()


def test_reapplying_the_same_plan_reuses_the_live_arena(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m = _Tiny()
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    streamer.apply("cpu", plan, pin=True)
    arena = streamer._arenas[0]
    streamer.apply("cpu", plan, pin=True)
    assert streamer._arenas == [arena]
    assert len(fake.registered) == 1 and fake.unregistered() == []
    assert all(arena.owns(p.data) for p in _streamed_params(m))
    streamer.teardown()
    assert fake.registered == {}


def _fake_device_moves(monkeypatch) -> None:
    real_move = partial._move_own_tensors

    def _move(module, device, **kwargs):
        if str(device) == "cpu":
            return real_move(module, device, **kwargs)
        for p in module.parameters(recurse=False):
            p.data = p.data.clone()

    monkeypatch.setattr(partial, "_move_own_tensors", _move)


def _segment_of(fake: _FakeCudaHost, tensor: torch.Tensor) -> int:
    [ptr] = [ptr for ptr, n in fake.registered.items() if ptr <= tensor.data_ptr() < ptr + n]
    return ptr


def test_reapplying_with_more_resident_leaves_frees_their_segments(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    _fake_device_moves(monkeypatch)
    m = _Tiny(n_linear=4, dim=32)
    leaf_bytes = 32 * 32 * 4 + 32 * 4
    monkeypatch.setattr(partial, "_TEARDOWN_RELEASE_CHUNK_GB", 2 * ARENA_ALIGNMENT / _GB)
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cuda:0", plan_residency_split(m, resident_budget_gb=0.0), pin=True)
    [arena] = streamer._arenas
    registers = fake.kinds().count("register")
    vacated = {_segment_of(fake, m.blocks[i].weight.data) for i in (0, 1)}
    kept = {_segment_of(fake, p.data) for p in _streamed_params(m)} - vacated
    assert len(vacated) == 2 and kept

    fixed_bytes = m.embed.weight.numel() * m.embed.weight.element_size()
    plan = plan_residency_split(m, resident_budget_gb=(fixed_bytes + 2 * leaf_bytes + 64) / _GB)
    assert plan.resident_names == ("blocks.0", "blocks.1")
    streamer.apply("cuda:0", plan, pin=True)

    assert sorted(fake.unregistered()) == sorted(vacated)
    assert set(fake.registered) == kept
    assert fake.kinds().count("register") == registers
    assert streamer._arenas == [arena]
    for i in (0, 1):
        assert not arena.owns(m.blocks[i].weight.data)
    assert all(arena.owns(m.blocks[i].weight.data) for i in (2, 3))
    assert streamer.pinned_gb * _GB == arena.pinned_bytes
    streamer.teardown()
    assert fake.registered == {}


def _parked_streamer(monkeypatch) -> tuple[_Tiny, ModuleStreamer, object]:
    _fake_device_moves(monkeypatch)
    monkeypatch.setattr(partial, "_release_idle_vram", lambda: None)
    monkeypatch.setattr(torch.Tensor, "pin_memory", lambda self: pytest.fail("per-tensor pin_memory used"))
    m = _Tiny(n_linear=4, dim=32)
    leaf_bytes = 32 * 32 * 4 + 32 * 4
    fixed_bytes = m.embed.weight.numel() * m.embed.weight.element_size()
    plan = plan_residency_split(m, resident_budget_gb=(fixed_bytes + 2 * leaf_bytes + 64) / _GB)
    streamer = ModuleStreamer(m, prefetch=False)
    streamer.apply("cuda:0", plan, pin=True)
    return m, streamer, plan


def test_park_pins_the_resident_weights_into_a_new_arena_and_keeps_the_streamed_one(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m, streamer, _ = _parked_streamer(monkeypatch)
    [streamed_arena] = streamer._arenas
    streamed_segments = set(fake.registered)

    assert streamer.park() is True

    assert streamer.parked and not streamer.active
    assert fake.unregistered() == []
    assert streamed_segments < set(fake.registered)
    assert streamer._arenas[0] is streamed_arena and len(streamer._arenas) == 2
    assert streamer._arenas[1]._device == torch.device("cuda:0")
    assert all(any(arena.owns(p.data) for arena in streamer._arenas) for p in m.parameters())
    assert streamer.parked_gb * _GB == sum(arena.pinned_bytes for arena in streamer._arenas)
    assert all(leaf.comfy_cast_weights is False for _, leaf in iter_streamable_leaves(m))


def test_park_hands_the_gpu_cache_back_once_the_resident_weights_left(monkeypatch):
    _fake_cuda_streamer(monkeypatch)
    m, streamer, _ = _parked_streamer(monkeypatch)
    seen = []
    monkeypatch.setattr(partial, "_release_idle_vram",
                        lambda: seen.append(all(any(arena.owns(p.data) for arena in streamer._arenas) for p in m.parameters())))

    streamer.park()

    assert seen == [True]


def test_placing_a_parked_module_again_registers_nothing_and_frees_the_resident_segments(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m, streamer, plan = _parked_streamer(monkeypatch)
    streamed_segments = set(fake.registered)
    streamer.park()
    registers = fake.kinds().count("register")

    streamer.apply("cuda:0", plan, pin=True)

    assert fake.kinds().count("register") == registers
    assert set(fake.registered) == streamed_segments
    assert streamer.active and not streamer.parked
    streamer.teardown()
    assert fake.registered == {}


@pytest.mark.parametrize("release", ["unpark", "forget_parked"])
def test_releasing_a_parked_module_unregisters_every_segment_of_its_arenas(monkeypatch, release):
    fake = _fake_cuda_streamer(monkeypatch)
    m, streamer, _ = _parked_streamer(monkeypatch)
    before = {name: p.data.clone() for name, p in m.named_parameters()}
    streamer.park()
    arenas = list(streamer._arenas)

    getattr(streamer, release)()

    assert fake.registered == {}
    assert [arena.registered_segments for arena in arenas] == [0, 0]
    assert streamer._arenas == [] and not streamer.parked and streamer.parked_gb == 0.0
    for name, p in m.named_parameters():
        assert torch.equal(p.data, before[name])


def test_a_parked_module_whose_placement_runs_out_of_memory_leaves_nothing_pinned(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m, streamer, plan = _parked_streamer(monkeypatch)
    before = {name: p.data.clone() for name, p in m.named_parameters()}
    streamer.park()
    real_move = partial._move_own_tensors

    def _oom_on_resident(module, device, **kwargs):
        if str(device) != "cpu":
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        real_move(module, device, **kwargs)

    monkeypatch.setattr(partial, "_move_own_tensors", _oom_on_resident)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        streamer.apply("cuda:0", plan, pin=True)

    assert fake.registered == {}
    assert streamer._arenas == []
    assert not streamer.active and not streamer.parked and streamer.parked_gb == 0.0
    assert all(leaf.comfy_cast_weights is False for _, leaf in iter_streamable_leaves(m))
    for name, p in m.named_parameters():
        assert torch.equal(p.data, before[name])


def test_a_placement_that_fails_midway_is_still_torn_down(monkeypatch):
    fake = _fake_cuda_streamer(monkeypatch)
    m = _Tiny()
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m)
    real_move = partial._move_own_tensors

    def _oom_on_resident(module, device, **kwargs):
        if str(device) != "cpu":
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        real_move(module, device, **kwargs)

    monkeypatch.setattr(partial, "_move_own_tensors", _oom_on_resident)
    with pytest.raises(torch.cuda.OutOfMemoryError):
        streamer.apply("cuda:0", plan, pin=True)

    assert fake.kinds().count("register") == 1
    assert fake.registered == {}
    assert streamer._arenas == []
    assert not streamer.active
    assert all(leaf.comfy_cast_weights is False for _, leaf in iter_streamable_leaves(m))


def test_is_host_pinned_sees_arena_memory_the_driver_flag_misses(monkeypatch):
    _FakeCudaHost(monkeypatch)
    plain = torch.randn(16)
    params = _params(torch.randn(64), torch.randn(8, 8))
    arena = PinnedArena(segment_bytes=_GB)
    arena.pin(params)

    assert not params[0].data.is_pinned()
    assert all(pinned_arena.is_host_pinned(p.data) for p in params)
    assert not pinned_arena.is_host_pinned(plain)

    assert arena.unpin(params[0])
    assert not pinned_arena.is_host_pinned(params[0].data)
    arena.release()
    assert not pinned_arena.is_host_pinned(params[1].data)
