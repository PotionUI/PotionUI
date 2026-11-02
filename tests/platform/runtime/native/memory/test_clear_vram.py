import pytest

import src.platform.runtime.model_lifecycle.lifecycle as lifecycle_module
import src.platform.runtime.native.memory.residency as residency_module
from src.platform.runtime.model_lifecycle.lifecycle import ModelLifecycle
from src.platform.runtime.native.memory.residency import ClearVramMode, GpuResidencyRegistry, clear_vram
from src.platform.runtime.system_memory import SystemMemory

GB = 1024 ** 3
TOTAL_RAM_GB = 64.0


class FakeRam:
    def __init__(self, available_gb):
        self.available_gb = available_gb
        self.reads = 0

    def read(self):
        self.reads += 1
        return SystemMemory(total=int(TOTAL_RAM_GB * GB), available=int(self.available_gb * GB))


class FakeComponent:
    def __init__(self, name, size_gb, ram, log):
        self.name = name
        self.estimated_vram_gb = size_gb
        self.device = "cuda:0"
        self.module = object()
        self._ram = ram
        self._log = log

    def offload(self):
        self._ram.available_gb -= self.estimated_vram_gb
        self.device = "cpu"
        self._log.append(("offload", self.name))

    def unload(self):
        self.module = None
        self.device = "cpu"
        self._log.append(("unload", self.name))


@pytest.fixture
def ram(monkeypatch):
    fake = FakeRam(0.0)
    monkeypatch.setattr(residency_module, "get_system_memory", fake.read)
    monkeypatch.setattr(
        lifecycle_module, "get_system_memory",
        lambda: SystemMemory(total=int(1024 * GB), available=int(1000 * GB)),
    )
    return fake


@pytest.fixture
def registry(monkeypatch):
    fresh = GpuResidencyRegistry()
    monkeypatch.setattr(residency_module, "get_residency_registry", lambda: fresh)
    return fresh


@pytest.fixture
def lifecycle():
    return ModelLifecycle(gpu_monitor=None, settings=None)


def _load(lifecycle, registry, ram, log, name, size_gb, *, register=True):
    component = lifecycle.acquire(name, "fp", lambda: FakeComponent(name, size_gb, ram, log), estimated_vram_gb=size_gb)
    if register:
        registry.note_resident(component, "cuda:0", size_gb)


def test_everything_offloads_when_ram_has_room(ram, registry, lifecycle):
    log = []
    ram.available_gb = 100.0
    _load(lifecycle, registry, ram, log, "dit", 20.0)
    _load(lifecycle, registry, ram, log, "te", 10.0)
    _load(lifecycle, registry, ram, log, "vae", 4.0)

    result = clear_vram("cuda:0", lifecycle)

    assert log == [("offload", "dit"), ("offload", "te"), ("offload", "vae")]
    assert (result.offloaded_count, result.unloaded_count, result.failed_count) == (3, 0, 0)
    assert result.offloaded_gb == pytest.approx(34.0)
    assert all(lifecycle.is_cached(key) for key in ("dit", "te", "vae"))
    assert registry.resident_gb("cuda:0") == 0.0


def test_everything_unloads_when_ram_has_no_room(ram, registry, lifecycle):
    log = []
    ram.available_gb = 10.0
    _load(lifecycle, registry, ram, log, "dit", 20.0)
    _load(lifecycle, registry, ram, log, "te", 10.0)
    _load(lifecycle, registry, ram, log, "vae", 4.0)

    result = clear_vram("cuda:0", lifecycle)

    assert log == [("unload", "dit"), ("unload", "te"), ("unload", "vae")]
    assert (result.offloaded_count, result.unloaded_count, result.failed_count) == (0, 3, 0)
    assert result.unloaded_gb == pytest.approx(34.0)
    assert result.freed_gb == pytest.approx(34.0)
    assert not any(lifecycle.is_cached(key) for key in ("dit", "te", "vae"))
    assert registry.resident_gb("cuda:0") == 0.0


def test_decision_switches_to_unload_once_offloads_fill_ram(ram, registry, lifecycle):
    log = []
    ram.available_gb = 40.0
    _load(lifecycle, registry, ram, log, "vae", 6.0)
    _load(lifecycle, registry, ram, log, "dit", 20.0)
    _load(lifecycle, registry, ram, log, "te", 10.0)

    result = clear_vram("cuda:0", lifecycle)

    assert log == [("offload", "dit"), ("offload", "te"), ("unload", "vae")]
    assert (result.offloaded_count, result.offloaded_gb) == (2, pytest.approx(30.0))
    assert (result.unloaded_count, result.unloaded_gb) == (1, pytest.approx(6.0))
    assert lifecycle.is_cached("dit") and lifecycle.is_cached("te")
    assert not lifecycle.is_cached("vae")
    assert ram.reads == 3


def test_leased_components_are_never_touched(ram, registry, lifecycle):
    log = []
    ram.available_gb = 100.0
    lifecycle.begin_lease("gen-1")
    try:
        _load(lifecycle, registry, ram, log, "busy-dit", 20.0)
        _load(lifecycle, registry, ram, log, "busy-straggler", 3.0, register=False)
        leased_id = id(lifecycle.leased_values()[0])

        for mode in ClearVramMode:
            result = clear_vram("cuda:0", lifecycle, mode)
            assert (result.offloaded_count, result.unloaded_count, result.failed_count) == (0, 0, 0)

        assert log == []
        assert registry.resident_gb("cuda:0") == pytest.approx(20.0)
        assert lifecycle.is_cached("busy-dit") and lifecycle.is_cached("busy-straggler")
        assert lifecycle.evict_value(leased_id) is None
    finally:
        lifecycle.end_lease("gen-1")


def test_lifecycle_sweep_follows_the_same_rule(ram, registry, lifecycle):
    log = []
    ram.available_gb = 20.0
    _load(lifecycle, registry, ram, log, "small", 6.0, register=False)
    _load(lifecycle, registry, ram, log, "big", 10.0, register=False)

    result = clear_vram("cuda:0", lifecycle)

    assert log == [("offload", "big"), ("unload", "small")]
    assert result.swept_count == 2
    assert (result.offloaded_count, result.unloaded_count) == (1, 1)
    assert lifecycle.is_cached("big") and not lifecycle.is_cached("small")


def test_offload_mode_ignores_ram(ram, registry, lifecycle):
    log = []
    ram.available_gb = 0.0
    _load(lifecycle, registry, ram, log, "dit", 20.0)
    _load(lifecycle, registry, ram, log, "straggler", 2.0, register=False)

    result = clear_vram("cuda:0", lifecycle, ClearVramMode.OFFLOAD)

    assert log == [("offload", "dit"), ("offload", "straggler")]
    assert (result.offloaded_count, result.unloaded_count, result.swept_count) == (2, 0, 1)
    assert ram.reads == 0


def test_unload_mode_ignores_ram(ram, registry, lifecycle):
    log = []
    ram.available_gb = 1000.0
    _load(lifecycle, registry, ram, log, "dit", 20.0)
    _load(lifecycle, registry, ram, log, "straggler", 2.0, register=False)

    result = clear_vram("cuda:0", lifecycle, ClearVramMode.UNLOAD)

    assert log == [("unload", "dit"), ("unload", "straggler")]
    assert (result.offloaded_count, result.unloaded_count, result.swept_count) == (0, 2, 1)
    assert ram.reads == 0


def test_a_component_still_held_elsewhere_is_reported_failed(ram, registry, lifecycle):
    log = []
    ram.available_gb = 0.0
    held = lifecycle.acquire("dit", "fp", lambda: FakeComponent("dit", 12.0, ram, log), estimated_vram_gb=12.0)
    registry.note_resident(held, "cuda:0", 12.0)

    result = clear_vram("cuda:0", lifecycle)

    assert log == []
    assert (result.unloaded_count, result.failed_count) == (0, 1)
    assert result.failed_gb == pytest.approx(12.0)
    assert held.device == "cuda:0"


def test_a_registered_inner_module_is_unloaded_through_its_cache_entry(ram, registry, lifecycle):
    log = []
    ram.available_gb = 0.0
    wrapper = lifecycle.acquire("te", "fp", lambda: FakeComponent("te", 8.0, ram, log), estimated_vram_gb=8.0)
    encoder = FakeComponent("te-inner", 8.0, ram, log)
    wrapper.module = encoder
    wrapper.device = "cpu"
    registry.note_resident(encoder, "cuda:0", 8.0)
    del wrapper

    result = clear_vram("cuda:0", lifecycle)

    assert log == [("unload", "te")]
    assert result.unloaded_count == 1
    assert not lifecycle.is_cached("te")
    assert registry.resident_gb("cuda:0") == 0.0
