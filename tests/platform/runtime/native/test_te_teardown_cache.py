from __future__ import annotations

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator, NativeModel, TextEncoderStreaming
from src.platform.runtime.native.memory.residency import get_residency_registry

_DEV = "cuda:0"


@pytest.fixture(autouse=True)
def _clean_registry():
    get_residency_registry().clear()
    yield
    get_residency_registry().clear()


class _Encoder:
    def __init__(self, log: list) -> None:
        self._log = log
        self.module = nn.Linear(4, 4)

    def to(self, device):
        self._log.append(f"encoder.to.{device}")
        return self


class _Card:
    def __init__(self, log: list, *, capacity_gb: float, cached_gb: float) -> None:
        self._log = log
        self.capacity_gb = capacity_gb
        self.cached_gb = cached_gb

    def free_vram_gb(self, device) -> float:
        return self.capacity_gb - self.cached_gb

    def empty_cache(self) -> None:
        self._log.append("empty_cache")
        self.cached_gb = 0.0


def _fake_cuda(monkeypatch, log: list, *, available: bool = True, cached_gb: float = 8.4) -> _Card:
    card = _Card(log, capacity_gb=10.6, cached_gb=cached_gb)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: available)
    monkeypatch.setattr(engine_mod.torch.cuda, "empty_cache", card.empty_cache)
    monkeypatch.setattr(engine_mod, "free_vram_gb", card.free_vram_gb)

    def stream_to(self, device, resident_budget_gb, *, non_blocking=True):
        log.append(f"part.stream_to.{device}")
        self.device = str(device)

    def offload(self):
        log.append("part.offload")
        self.device = "cpu"

    monkeypatch.setattr(NativeModel, "stream_to", stream_to)
    monkeypatch.setattr(NativeModel, "offload", offload)
    return card


def _streaming(log: list) -> TextEncoderStreaming:
    encoder = _Encoder(log)
    return TextEncoderStreaming(encoder, modules=[encoder.module])


def test_the_streamed_text_encoder_teardown_returns_the_idle_cache_after_offloading_every_part(monkeypatch):
    log: list = []
    card = _fake_cuda(monkeypatch, log)
    streaming = _streaming(log)
    streaming.apply(_DEV, 0.0)
    log.clear()

    streaming.teardown()

    assert log == ["part.offload", "encoder.to.cpu", "empty_cache"]
    assert engine_mod.free_vram_gb(_DEV) == pytest.approx(card.capacity_gb)


def test_the_generator_offload_of_a_streamed_text_encoder_returns_the_idle_cache(monkeypatch):
    log: list = []
    _fake_cuda(monkeypatch, log)
    streaming = _streaming(log)
    streaming.apply(_DEV, 0.0)
    gen = object.__new__(NativeGenerator)
    gen._te_streaming = streaming
    log.clear()

    gen._maybe_offload_te()

    assert gen._te_streaming is None
    assert log[-1] == "empty_cache"


def test_the_generator_offload_of_a_text_encoder_that_was_not_streamed_does_not_empty_the_cache(monkeypatch):
    log: list = []
    _fake_cuda(monkeypatch, log)
    encoder = _Encoder(log)
    gen = object.__new__(NativeGenerator)
    gen._te_streaming = None
    gen.te = encoder
    monkeypatch.setattr(gen, "_is_cuda", lambda: True)

    gen._maybe_offload_te()

    assert log == ["encoder.to.cpu"]


def test_a_streaming_that_was_never_applied_tears_down_without_touching_the_allocator(monkeypatch):
    log: list = []
    _fake_cuda(monkeypatch, log)
    streaming = _streaming(log)

    streaming.teardown()

    assert "empty_cache" not in log


@pytest.mark.parametrize(("device", "available"), [("cpu", True), (_DEV, False)])
def test_the_teardown_never_calls_the_cuda_allocator_off_cuda(monkeypatch, device, available):
    log: list = []
    _fake_cuda(monkeypatch, log, available=available)
    streaming = _streaming(log)
    streaming.apply(device, 0.0)

    streaming.teardown()

    assert "empty_cache" not in log


def test_the_cache_is_still_returned_when_a_part_fails_to_offload_and_the_first_error_is_raised(monkeypatch):
    log: list = []
    _fake_cuda(monkeypatch, log)
    streaming = _streaming(log)
    streaming.apply(_DEV, 0.0)
    first = RuntimeError("offload failed")

    def failing_offload(self):
        log.append("part.offload")
        raise first

    monkeypatch.setattr(NativeModel, "offload", failing_offload)
    log.clear()

    with pytest.raises(RuntimeError, match="offload failed") as excinfo:
        streaming.teardown()

    assert excinfo.value is first
    assert log == ["part.offload", "encoder.to.cpu", "empty_cache"]


def test_an_empty_cache_failure_does_not_mask_the_first_teardown_error(monkeypatch):
    log: list = []
    _fake_cuda(monkeypatch, log)
    streaming = _streaming(log)
    streaming.apply(_DEV, 0.0)
    first = RuntimeError("offload failed")

    def failing_offload(self):
        raise first

    def failing_empty_cache():
        raise RuntimeError("empty_cache failed")

    monkeypatch.setattr(NativeModel, "offload", failing_offload)
    monkeypatch.setattr(engine_mod.torch.cuda, "empty_cache", failing_empty_cache)

    with pytest.raises(RuntimeError, match="offload failed") as excinfo:
        streaming.teardown()

    assert excinfo.value is first
