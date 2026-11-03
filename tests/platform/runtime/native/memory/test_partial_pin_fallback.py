from __future__ import annotations

import logging

import pytest
import torch

from src.platform.runtime.native.memory import partial
from src.platform.runtime.native.memory.partial import (
    ModuleStreamer,
    PinnedMemoryUnavailableError,
    plan_residency_split,
)
from src.platform.runtime.native.memory.pinned_arena import caching_host_block_bytes

from .._pin_fakes import FakePinning, hooks_in, streamed_flags_in
from .test_partial_nvfp4 import _QuantTE, _tokens

GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _fresh_warning(monkeypatch):
    monkeypatch.setattr(partial, "_warned_pin_fallback", False)


def _streamed_leaves(m: _QuantTE):
    return [m.nv_in, m.int8, m.fp8, m.plain, m.norm, m.nv_out]


def _pinned_bytes(m: _QuantTE) -> int:
    tensors = list(m.parameters()) + list(m.buffers())
    return sum(caching_host_block_bytes(t.numel() * t.element_size()) for t in tensors if t.is_pinned())


def _forward(m: _QuantTE) -> torch.Tensor:
    with torch.inference_mode():
        return m(_tokens())


def _fail_once_at(monkeypatch, target, error: BaseException, *, when_pinning: bool):
    real = partial._move_own_tensors
    pending = [error]

    def wrapper(module, device, *, pin: bool, **kwargs):
        if module is target and pin is when_pinning and pending:
            raise pending.pop()
        return real(module, device, pin=pin, **kwargs)

    monkeypatch.setattr(partial, "_move_own_tensors", wrapper)


def test_pin_helper_raises_a_dedicated_error_and_counts_nothing_when_refused(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)

    with pytest.raises(PinnedMemoryUnavailableError, match="out of memory") as caught:
        partial._pin(torch.zeros(4))

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert isinstance(caught.value, RuntimeError)
    assert fake.counted == []


def test_pin_helper_counts_exactly_the_bytes_it_pinned(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)

    pinned = partial._pin(torch.zeros(4, dtype=torch.float32))

    assert pinned.is_pinned()
    assert fake.counted == [16]


def test_a_refused_pin_places_the_whole_plan_unpinned_and_the_forward_is_unchanged(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)
    m = _QuantTE()
    reference = _forward(m)
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)

    streamer.apply("cpu", plan, pin=True)

    assert fake.attempts >= 1
    assert streamer.active
    assert streamer.pinned_gb == 0.0
    assert streamer.streamed_gb == plan.streamed_bytes / GB
    assert fake.pinned_in(m) == 0
    assert fake.counted == []
    assert [leaf.stream_non_blocking for leaf in _streamed_leaves(m)] == [False] * 6
    assert all(leaf.comfy_cast_weights for leaf in _streamed_leaves(m))
    assert torch.equal(_forward(m), reference)
    streamer.teardown()
    assert torch.equal(_forward(m), reference)
    assert streamed_flags_in(m) == 0


def test_a_pin_refused_midway_releases_what_was_pinned_and_counts_only_the_successes(monkeypatch):
    fake = FakePinning(monkeypatch, allow=5)
    m = _QuantTE()
    reference = _forward(m)
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)

    streamer.apply("cpu", plan, pin=True)

    assert fake.attempts == 6
    assert fake.pins == 5
    assert len(fake.counted) == 5
    assert fake.pinned_in(m) == 0
    assert fake.pool_releases == 1
    assert streamer.active
    assert streamer.pinned_gb == 0.0
    assert hooks_in(m) == 4
    assert torch.equal(_forward(m), reference)
    streamer.teardown()
    assert hooks_in(m) == 0


def test_a_refused_pin_on_the_second_buffer_of_an_nvfp4_leaf_leaves_none_of_it_pinned(monkeypatch):
    fake = FakePinning(monkeypatch, allow=1)
    m = _QuantTE()
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)

    streamer.apply("cpu", plan, pin=True)

    assert fake.pins == 1
    assert fake.pinned_in(m.nv_in) == 0
    assert fake.pinned_in(m) == 0


def test_pinning_that_succeeds_is_untouched(monkeypatch, caplog):
    fake = FakePinning(monkeypatch, allow=None)
    m = _QuantTE()
    reference = _forward(m)
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)

    with caplog.at_level(logging.INFO, logger=partial.logger.name):
        streamer.apply("cpu", plan, pin=True)

    assert fake.pins == fake.attempts > 0
    assert len(fake.counted) == fake.pins
    assert 0 < _pinned_bytes(m) <= plan.streamed_bytes
    assert streamer.pinned_gb == _pinned_bytes(m) / GB
    assert [leaf.stream_non_blocking for leaf in _streamed_leaves(m)] == [True] * 6
    assert fake.pool_releases == 0
    assert not [r for r in caplog.records if r.name == partial.logger.name and "pinned host memory" in r.getMessage()]
    assert torch.equal(_forward(m), reference)
    streamer.teardown()
    assert fake.pinned_in(m) == 0


def test_a_caller_that_asked_for_no_pinning_never_touches_pin_memory(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    m = _QuantTE()
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)

    ModuleStreamer(m, stream_nvfp4=True).apply("cpu", plan, pin=False)

    assert fake.attempts == 0


def test_the_fallback_warns_once_then_stays_at_info(monkeypatch, caplog):
    FakePinning(monkeypatch, allow=0)

    with caplog.at_level(logging.INFO, logger=partial.logger.name):
        for _ in range(2):
            m = _QuantTE()
            plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
            ModuleStreamer(m, stream_nvfp4=True).apply("cpu", plan, pin=True)

    ours = [r for r in caplog.records if "pinned host memory unavailable" in r.getMessage()]
    assert [r.levelno for r in ours] == [logging.WARNING, logging.INFO]
    assert "synchronous" in ours[0].getMessage()
    assert "out of memory" in ours[0].getMessage()


def test_any_other_failed_placement_is_rolled_back_completely(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    m = _QuantTE()
    reference = _forward(m)
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)
    _fail_once_at(monkeypatch, m.plain, ValueError("boom"), when_pinning=True)

    with pytest.raises(ValueError, match="boom"):
        streamer.apply("cpu", plan, pin=True)

    assert fake.pins > 0
    assert not streamer.active
    assert fake.pinned_in(m) == 0
    assert hooks_in(m) == 0
    assert streamed_flags_in(m) == 0
    assert torch.equal(_forward(m), reference)


def test_an_unpinned_retry_that_fails_is_rolled_back_and_propagates(monkeypatch):
    fake = FakePinning(monkeypatch, allow=3)
    m = _QuantTE()
    plan = plan_residency_split(m, 0.0, stream_nvfp4=True)
    streamer = ModuleStreamer(m, stream_nvfp4=True)
    _fail_once_at(monkeypatch, m.plain, torch.cuda.OutOfMemoryError("fake"), when_pinning=False)

    with pytest.raises(torch.cuda.OutOfMemoryError):
        streamer.apply("cpu", plan, pin=True)

    assert fake.pins == 3
    assert not streamer.active
    assert fake.pinned_in(m) == 0
    assert hooks_in(m) == 0
    assert streamed_flags_in(m) == 0
