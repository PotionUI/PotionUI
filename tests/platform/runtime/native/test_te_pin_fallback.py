from __future__ import annotations

import logging

import pytest
import torch

from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import TextEncoderStreaming
from src.platform.runtime.native.kernels import sync_nvfp4_dequant_tables
from src.platform.runtime.native.memory import partial, residency
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from src.platform.runtime.native.memory.residency import get_residency_registry, run_text_encode
from src.platform.runtime.native.memory.text_encoder import reset_text_encoder_placement
from src.platform.runtime.native.memory.tiering import ComponentPlacement, PlacementPlan

from ._pin_fakes import FakePinning, hooks_in, streamed_flags_in
from ._te_layouts import FamilyEncoder
from .test_te_streaming import _DEV, GB, _fake_card, _generator, _RealEncoder, _RealTE


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_text_encoder_placement()
    get_residency_registry().clear()
    monkeypatch.setattr(partial, "_warned_pin_fallback", False)
    yield
    reset_text_encoder_placement()
    get_residency_registry().clear()


class _CpuStreamer(ModuleStreamer):
    def apply(self, device, plan, *, pin=True, non_blocking=True):
        super().apply("cpu", plan, pin=pin, non_blocking=non_blocking)


class _NoRoomUnpinnedStreamer(_CpuStreamer):
    def _place(self, device, plan, *, pin, non_blocking):
        if not pin and plan.resident_bytes:
            raise torch.cuda.OutOfMemoryError("fake unpinned OOM")
        super()._place(device, plan, pin=pin, non_blocking=non_blocking)


class _NeverUnpinnedStreamer(_CpuStreamer):
    def _place(self, device, plan, *, pin, non_blocking):
        if not pin:
            raise torch.cuda.OutOfMemoryError("fake unpinned OOM")
        super()._place(device, plan, pin=pin, non_blocking=non_blocking)


def _use_streamer(monkeypatch, streamer_cls=_CpuStreamer):
    monkeypatch.setattr(engine_mod, "ModuleStreamer", streamer_cls)


def _single(log):
    module = _RealTE(log)
    encoder = _RealEncoder(module, log, "probe")
    sync_nvfp4_dequant_tables(module)
    fixed = plan_residency_split(module, 0.0, stream_nvfp4=True).fixed_bytes
    one_block = sum(t.numel() * t.element_size() for t in module.blocks[0].buffers())
    return encoder, module, (fixed + one_block) / GB


class _Composite:
    def __init__(self, t5: FamilyEncoder, clip: FamilyEncoder) -> None:
        self.t5 = t5
        self.clip_l = clip

    def to(self, device):
        self.t5.to(device)
        self.clip_l.to(device)
        return self

    def encode(self, texts):
        return self.t5.encode(texts)


def _composite(log):
    t5 = _RealEncoder(_RealTE(log, "t5", layers=6), log, "t5xxl")
    clip = _RealEncoder(_RealTE(log, "clip_l", layers=1), log, "clip_l")
    clip_size = sum(
        t.numel() * t.element_size() for t in list(clip.module.parameters()) + list(clip.module.buffers())
    )
    t5_fixed = plan_residency_split(t5.module, 0.0, stream_nvfp4=True).fixed_bytes
    return _Composite(t5, clip), t5, clip, (clip_size + t5_fixed + 1024) / GB


def _reference(encoder) -> torch.Tensor:
    with torch.inference_mode():
        return encoder.encode(["p"])["context"]


def _pipe_card(monkeypatch, log, budget_gb: float):
    registry, _ = _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    monkeypatch.setattr(residency, "module_size_gb", lambda enc: 19.0)
    streamed = ComponentPlacement(_DEV, "standard", True, streamed=True, stream_budget_gb=budget_gb)
    monkeypatch.setattr(residency, "plan_text_encoder_placement", lambda *args, **kwargs: streamed)
    return registry


def _generator_for(encoder, budget_gb: float):
    whole = ComponentPlacement(_DEV, "standard", True)
    streamed = ComponentPlacement(_DEV, "standard", True, streamed=True, stream_budget_gb=budget_gb)
    return _generator(encoder, vram_gb=12.0, placement=PlacementPlan(whole, streamed, whole, False, "resident"))


def _warnings(caplog):
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "pinned host memory unavailable" in r.getMessage()
    ]


def _fail_when_pinning(monkeypatch, target, errors: list):
    pending = list(errors)

    def guarded(real):
        def wrapper(module, device, *, pin, **kwargs):
            if module is target and pin and pending:
                raise pending.pop(0)
            return real(module, device, pin=pin, **kwargs)

        return wrapper

    monkeypatch.setattr(partial, "_move_own_tensors", guarded(partial._move_own_tensors))
    monkeypatch.setattr(partial, "_place_streamed_nvfp4", guarded(partial._place_streamed_nvfp4))


def _assert_nothing_left_behind(fake, *modules):
    assert fake.pinned_in(*modules) == 0
    assert hooks_in(*modules) == 0
    assert streamed_flags_in(*modules) == 0
    assert get_residency_registry().resident_gb(_DEV) == 0.0


def test_pipe_path_streams_unpinned_when_the_host_refuses_to_pin(monkeypatch, caplog):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    log.clear()
    _pipe_card(monkeypatch, log, budget)
    copy_modes: list[list[bool]] = []

    def encode():
        copy_modes.append([block.stream_non_blocking for block in module.blocks])
        return encoder.encode(["p"])

    with torch.inference_mode(), caplog.at_level(logging.INFO, logger=partial.logger.name):
        out = run_text_encode(encoder, _DEV, encode)

    assert torch.equal(out["context"], reference)
    assert ("mark", "streamed", pytest.approx(budget, abs=1e-12)) in log
    assert not [e for e in log if e[:2] == ("mark", "cpu-fallback")]
    assert copy_modes == [[False, False, False, False]]
    assert len(_warnings(caplog)) == 1
    assert fake.counted == []
    _assert_nothing_left_behind(fake, module)
    assert encoder._device == torch.device("cpu")


def test_pipe_path_pinned_streaming_is_unchanged_when_the_host_pins(monkeypatch, caplog):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    log.clear()
    _pipe_card(monkeypatch, log, budget)
    pinned_during: list[int] = []
    copy_modes: list[list[bool]] = []

    def encode():
        pinned_during.append(fake.pinned_in(module))
        copy_modes.append([block.stream_non_blocking for block in module.blocks])
        return encoder.encode(["p"])

    with torch.inference_mode(), caplog.at_level(logging.INFO, logger=partial.logger.name):
        out = run_text_encode(encoder, _DEV, encode)

    assert torch.equal(out["context"], reference)
    assert pinned_during[0] > 0
    assert copy_modes == [[False, True, True, True]]
    assert _warnings(caplog) == []
    assert fake.pool_releases == 1
    assert len(fake.counted) == fake.pins > 0
    _assert_nothing_left_behind(fake, module)


def test_pipe_path_reaches_the_cpu_encode_when_the_unpinned_retry_keeps_failing(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch, _NeverUnpinnedStreamer)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    log.clear()
    _pipe_card(monkeypatch, log, budget)

    with torch.inference_mode():
        out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))

    assert torch.equal(out["context"], reference)
    assert log[-2:] == [("mark", "cpu-fallback", None), ("encode", "cpu")]
    _assert_nothing_left_behind(fake, module)


def test_pipe_path_retries_fully_streamed_when_the_unpinned_share_does_not_fit(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch, _NoRoomUnpinnedStreamer)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    log.clear()
    _pipe_card(monkeypatch, log, budget)

    with torch.inference_mode():
        out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))

    assert torch.equal(out["context"], reference)
    assert ("mark", "streamed", 0.0) in log
    _assert_nothing_left_behind(fake, module)


def test_pipe_path_undoes_a_composite_placement_that_fails_midway(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)
    _pipe_card(monkeypatch, log, budget)
    _fail_when_pinning(monkeypatch, t5.module.blocks[3], [RuntimeError("placement boom")])

    with pytest.raises(RuntimeError, match="placement boom"):
        run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))

    assert fake.pins > 0
    _assert_nothing_left_behind(fake, t5.module, clip.module)
    assert t5._device == torch.device("cpu") and clip._device == torch.device("cpu")


def test_pipe_path_encodes_again_after_a_placement_that_failed_midway(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)
    reference = _reference(encoder)
    _pipe_card(monkeypatch, log, budget)
    _fail_when_pinning(monkeypatch, t5.module.blocks[3], [RuntimeError("placement boom")])

    with pytest.raises(RuntimeError, match="placement boom"):
        run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))
    _assert_nothing_left_behind(fake, t5.module, clip.module)

    with torch.inference_mode():
        out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))

    assert torch.equal(out["context"], reference)
    assert [e for e in log if e[:2] == ("mark", "streamed")]
    _assert_nothing_left_behind(fake, t5.module, clip.module)


def test_pipe_path_encodes_again_after_an_encode_that_failed(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    _pipe_card(monkeypatch, log, budget)

    def failing():
        raise RuntimeError("encode boom")

    with pytest.raises(RuntimeError, match="encode boom"):
        run_text_encode(encoder, _DEV, failing)
    _assert_nothing_left_behind(fake, module)
    assert encoder._device == torch.device("cpu")

    with torch.inference_mode():
        out = run_text_encode(encoder, _DEV, lambda: encoder.encode(["p"]))

    assert torch.equal(out["context"], reference)
    _assert_nothing_left_behind(fake, module)


def test_generator_path_streams_unpinned_when_the_host_refuses_to_pin(monkeypatch, caplog):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    gen = _generator_for(encoder, budget)
    copy_modes: list[list[bool]] = []
    real_encode = encoder.encode

    def encode(texts):
        copy_modes.append([block.stream_non_blocking for block in module.blocks])
        return real_encode(texts)

    encoder.encode = encode

    with torch.inference_mode(), caplog.at_level(logging.INFO, logger=partial.logger.name):
        conditioning = gen.encode_prompt("prompt")

    assert torch.equal(conditioning.cond["context"], reference)
    assert copy_modes == [[False, False, False, False]]
    assert len(_warnings(caplog)) == 1
    assert fake.counted == []
    assert gen._te_streaming is None
    _assert_nothing_left_behind(fake, module)


def test_generator_path_pinned_streaming_is_unchanged_when_the_host_pins(monkeypatch, caplog):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    gen = _generator_for(encoder, budget)
    real_encode = encoder.encode
    pinned_during: list[int] = []

    def encode(texts):
        pinned_during.append(fake.pinned_in(module))
        return real_encode(texts)

    encoder.encode = encode

    with torch.inference_mode(), caplog.at_level(logging.INFO, logger=partial.logger.name):
        conditioning = gen.encode_prompt("prompt")

    assert torch.equal(conditioning.cond["context"], reference)
    assert pinned_during[0] > 0
    assert _warnings(caplog) == []
    _assert_nothing_left_behind(fake, module)


def test_generator_path_reaches_the_fully_streamed_retry_when_the_unpinned_share_does_not_fit(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch, _NoRoomUnpinnedStreamer)
    log: list = []
    encoder, module, budget = _single(log)
    reference = _reference(encoder)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    gen = _generator_for(encoder, budget)

    with torch.inference_mode():
        conditioning = gen.encode_prompt("prompt")

    assert torch.equal(conditioning.cond["context"], reference)
    assert fake.attempts >= 2
    assert gen._te_streaming is None
    _assert_nothing_left_behind(fake, module)


def test_generator_path_undoes_a_composite_placement_that_fails_midway(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    _fail_when_pinning(monkeypatch, t5.module.blocks[3], [RuntimeError("placement boom")])
    gen = _generator_for(encoder, budget)

    with pytest.raises(RuntimeError, match="placement boom"):
        gen.encode_prompt("prompt")

    assert fake.pins > 0
    assert gen._te_streaming is None
    _assert_nothing_left_behind(fake, t5.module, clip.module)
    assert t5._device == torch.device("cpu") and clip._device == torch.device("cpu")


def test_generator_path_encodes_again_after_a_placement_that_failed_midway(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)
    reference = _reference(encoder)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    _fail_when_pinning(monkeypatch, t5.module.blocks[3], [RuntimeError("placement boom")])
    gen = _generator_for(encoder, budget)

    with pytest.raises(RuntimeError, match="placement boom"):
        gen.encode_prompt("prompt")
    _assert_nothing_left_behind(fake, t5.module, clip.module)

    with torch.inference_mode():
        conditioning = gen.encode_prompt("prompt")

    assert torch.equal(conditioning.cond["context"], reference)
    assert gen._te_streaming is None
    _assert_nothing_left_behind(fake, t5.module, clip.module)


def test_generator_path_undoes_the_placement_when_the_fully_streamed_retry_fails_too(monkeypatch):
    fake = FakePinning(monkeypatch, allow=None)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)
    _fake_card(monkeypatch, log, capacity_gb=12.0, others_gb=2.0)
    _fail_when_pinning(
        monkeypatch, t5.module.blocks[3], [torch.cuda.OutOfMemoryError("fake"), RuntimeError("retry boom")],
    )
    gen = _generator_for(encoder, budget)

    with pytest.raises(RuntimeError, match="retry boom"):
        gen.encode_prompt("prompt")

    assert gen._te_streaming is None
    _assert_nothing_left_behind(fake, t5.module, clip.module)
    assert t5._device == torch.device("cpu") and clip._device == torch.device("cpu")


def test_the_streaming_object_itself_places_a_composite_unpinned_when_pinning_is_refused(monkeypatch):
    fake = FakePinning(monkeypatch, allow=0)
    _use_streamer(monkeypatch)
    log: list = []
    encoder, t5, clip, budget = _composite(log)

    streaming = TextEncoderStreaming(encoder)
    streaming.apply(_DEV, budget)

    assert streaming.active
    assert streaming.streamed_gb > 0.0
    assert fake.pinned_in(t5.module, clip.module) == 0
    streaming.teardown()
    _assert_nothing_left_behind(fake, t5.module, clip.module)
