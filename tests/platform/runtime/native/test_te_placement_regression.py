from __future__ import annotations

import pytest
import torch

from src.platform.observability import profiling
from src.platform.runtime.native import engine as engine_mod
from src.platform.runtime.native.engine import NativeGenerator, NativeModel
from src.platform.runtime.native.memory import residency
from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.residency import module_size_gb, run_text_encode, run_text_encode_batch
from src.platform.runtime.native.memory.tiering import ComponentPlacement, PlacementPlan

from ._te_layouts import FAMILIES, RecordingModule

_DEV = "cuda:0"
_OTHERS_GB = 0.5


class _Recorder:
    def __init__(self, log: list) -> None:
        self._log = log

    def mark(self, name, **fields):
        if name == "te.encode":
            self._log.append((
                "mark", fields.get("path"), fields.get("device"),
                round(float(fields.get("size_gb") or 0.0), 6),
                None if fields.get("free_gb") is None else round(float(fields["free_gb"]), 6),
                None if fields.get("needed_gb") is None else round(float(fields["needed_gb"]), 6),
                fields.get("count"),
            ))


class _ResidentDiT:
    def __init__(self, log: list) -> None:
        self._log = log

    def offload(self) -> None:
        self._log.append(("dit.offload",))


def _fake_card(monkeypatch, log: list, capacity_gb: float):
    registry = residency.get_residency_registry()
    registry.clear()

    def free(device):
        return capacity_gb - _OTHERS_GB - registry.resident_gb(device)

    def note_resident(model, device, size_gb, _orig=registry.note_resident):
        log.append(("note_resident", type(model).__name__, str(device), round(float(size_gb or 0.0), 6)))
        _orig(model, device, size_gb)

    def note_offloaded(model, _orig=registry.note_offloaded):
        log.append(("note_offloaded", type(model).__name__))
        _orig(model)

    def ensure_free(device, need_gb, current_free_gb, *, exclude=(), _orig=registry.ensure_free):
        log.append(("ensure_free", str(device), round(float(need_gb), 6), round(float(current_free_gb), 6)))
        return _orig(device, need_gb, current_free_gb, exclude=exclude)

    monkeypatch.setattr(registry, "note_resident", note_resident)
    monkeypatch.setattr(registry, "note_offloaded", note_offloaded)
    monkeypatch.setattr(registry, "ensure_free", ensure_free)
    monkeypatch.setattr(residency.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(residency.torch.cuda, "empty_cache", lambda: log.append(("empty_cache",)))
    monkeypatch.setattr(residency, "free_vram_gb", free)
    monkeypatch.setattr(engine_mod, "free_vram_gb", free)
    monkeypatch.setattr(profiling, "get_profiler", lambda: _Recorder(log))
    return registry


def _expected_whole_move(encoder, size_gb: float, *, free_gb: float, path: str, count=None) -> list:
    log: list = []
    probe = []
    if hasattr(encoder, "t5"):
        probe += [("encoder.to", _DEV), ("encoder.to", _DEV), ("t5.to", _DEV), ("encoder.to", _DEV), ("clip_l.to", _DEV)]
        probe += [("t5.to", _DEV), ("clip_l.to", _DEV)]
    else:
        probe += [("encoder.to", _DEV), ("module.to", _DEV), ("module.to", _DEV)]
    log += probe
    log.append(("note_resident", type(encoder).__name__, _DEV, round(size_gb, 6)))
    log += [("encode", _DEV)] * (count or 1)
    log.append(("mark", path, _DEV, round(size_gb, 6), round(free_gb, 6), round(size_gb + 1.0, 6), count))
    return log


def _encode_fn(encoder):
    return lambda: encoder.encode(["a prompt"])


@pytest.fixture(autouse=True)
def _clean_registry():
    residency.get_residency_registry().clear()
    yield
    residency.get_residency_registry().clear()


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.family)
def test_fitting_encoder_coresides_with_the_exact_historical_sequence(monkeypatch, family):
    log: list = []
    registry = _fake_card(monkeypatch, log, family.card_gb)
    dit = _ResidentDiT(log)
    registry.note_resident(dit, _DEV, 1.0)
    log.clear()
    encoder = family.build(log)
    size = module_size_gb(encoder)
    free = family.card_gb - _OTHERS_GB - 1.0
    assert free >= size + 1.0

    run_text_encode(encoder, _DEV, _encode_fn(encoder))

    assert log == _expected_whole_move(encoder, size, free_gb=free, path="co-resident")


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.family)
def test_fitting_encoder_after_eviction_keeps_the_exact_historical_sequence(monkeypatch, family):
    log: list = []
    encoder = family.build(log)
    size = module_size_gb(encoder)
    registry = _fake_card(monkeypatch, log, family.card_gb)
    dit_gb = family.card_gb - _OTHERS_GB - size
    dit = _ResidentDiT(log)
    registry.note_resident(dit, _DEV, dit_gb)
    log.clear()

    run_text_encode(encoder, _DEV, _encode_fn(encoder))

    expected = [
        ("ensure_free", _DEV, round(size * 1.5 + 1.0, 6), round(size, 6)),
        ("dit.offload",),
        ("empty_cache",),
    ] + _expected_whole_move(encoder, size, free_gb=size, path="after-evict")
    assert log == expected


@pytest.mark.parametrize("family", FAMILIES[:3], ids=lambda f: f.family)
def test_fitting_encoder_batch_window_keeps_the_exact_historical_sequence(monkeypatch, family):
    log: list = []
    registry = _fake_card(monkeypatch, log, family.card_gb)
    dit = _ResidentDiT(log)
    registry.note_resident(dit, _DEV, 1.0)
    log.clear()
    encoder = family.build(log)
    size = module_size_gb(encoder)

    run_text_encode_batch(encoder, _DEV, [_encode_fn(encoder), _encode_fn(encoder)])

    free = family.card_gb - _OTHERS_GB - 1.0
    assert log == _expected_whole_move(encoder, size, free_gb=free, path="co-resident", count=2)


class _FakeSpec:
    family = "fake"
    variant = "fake"
    latent_format: dict = {}
    sampling_settings: dict = {}


class _FakeNativeModel:
    def __init__(self, kind: str, gb: float) -> None:
        self.kind = kind
        self.spec = _FakeSpec() if kind == "dit" else None
        self.estimated_vram_gb = gb
        self.quant_format = None
        self.compute_dtype = torch.float32
        self.module = object()

    def move_to(self, device) -> None:
        pass

    def offload(self) -> None:
        pass


@pytest.mark.parametrize("family", FAMILIES, ids=lambda f: f.family)
def test_generator_encode_prompt_moves_a_fitting_encoder_whole(monkeypatch, family):
    log: list = []
    _fake_card(monkeypatch, log, family.card_gb)
    monkeypatch.setattr(engine_mod.torch.cuda, "is_available", lambda: True)
    encoder = family.build(log)
    gen = NativeGenerator(
        _FakeNativeModel("dit", 4.0), encoder, _FakeNativeModel("vae", 0.3),
        DevicePlan(_DEV, _DEV, _DEV), vram_gb=family.card_gb,
    )

    gen.encode_prompt("a prompt")

    if hasattr(encoder, "t5"):
        up = [("encoder.to", _DEV), ("encoder.to", _DEV), ("t5.to", _DEV), ("encoder.to", _DEV), ("clip_l.to", _DEV)]
        down = [("encoder.to", "cpu"), ("encoder.to", "cpu"), ("t5.to", "cpu"), ("encoder.to", "cpu"), ("clip_l.to", "cpu")]
    else:
        up = [("encoder.to", _DEV), ("module.to", _DEV)]
        down = [("encoder.to", "cpu"), ("module.to", "cpu")]
    assert log == up + [("encode", _DEV)] + down


def test_seedvr2_without_a_text_encoder_keeps_its_sampling_plan(monkeypatch):
    gen = NativeGenerator(
        _FakeNativeModel("dit", 6.6), None, _FakeNativeModel("vae", 0.5),
        DevicePlan(_DEV, _DEV, _DEV), vram_gb=12.0,
    )

    plan = gen._build_placement((1, 16, 1, 90, 160))

    assert plan == PlacementPlan(
        dit=ComponentPlacement(_DEV, "standard", True),
        text_encoder=ComponentPlacement(_DEV, "standard", True),
        vae=ComponentPlacement(_DEV, "standard", True),
        vae_tiling=False,
        tier="resident",
    )


def test_music3_text_encoder_native_model_moves_and_offloads_as_before(monkeypatch):
    log: list = []
    registry = _fake_card(monkeypatch, log, 24.0)
    module = RecordingModule(log)
    model = NativeModel("text_encoder", module, estimated_vram_gb=None)

    model.move_to(_DEV)
    model.offload()

    assert log == [
        ("module.to", _DEV),
        ("note_resident", "NativeModel", _DEV, 0.0),
        ("module.to", "cpu"),
        ("note_offloaded", "NativeModel"),
    ]
    assert registry.resident_gb(_DEV) == 0.0
