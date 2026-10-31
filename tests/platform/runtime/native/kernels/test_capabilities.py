from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import inspect
import sys
import threading
import time
import types

import pytest
import torch

from src.platform.runtime.native.kernels import (
    FLASH,
    SAGE,
    SAGE2,
    SAGE3,
    SDPA,
    SPARGE,
    GpuGeneration,
    capabilities,
    hardware,
    refresh_capabilities,
)
from src.platform.runtime.native.kernels.hardware import _install_snapshot
from tests.fixtures.kernel_capabilities import (
    ada,
    ampere,
    blackwell,
    cpu_only,
    fake_capabilities,
    fake_device,
    hopper,
    installed_capabilities,
)

PROBED = frozenset({"triton", "sageattention", "flash_attn", "sageattn3", "spas_sage_attn"})


@pytest.fixture(autouse=True)
def _fresh_snapshot():
    previous = _install_snapshot(None)
    yield
    _install_snapshot(previous)


def _raise(*args, **kwargs):
    raise RuntimeError("driver hiccup")


def _environment(monkeypatch, *, devices=(), modules=(), sage_v2=True, runtime="12.8", current=0, memory=24 * 1024 ** 3, hip=None):
    state = types.SimpleNamespace(current=current, availability_calls=0, device_count_calls=0)

    def is_available():
        state.availability_calls += 1
        return bool(devices)

    def device_count():
        state.device_count_calls += 1
        return len(devices)

    def get_device_capability(index=None):
        entry = devices[state.current if index is None else index]
        if isinstance(entry, Exception):
            raise entry
        return entry

    def get_device_name(index=None):
        return f"GPU {state.current if index is None else index}"

    real_find_spec = importlib.util.find_spec
    real_version = importlib.metadata.version

    def find_spec(name, package=None):
        if name in PROBED:
            return object() if name in modules else None
        return real_find_spec(name, package)

    def version(name):
        if name == "sageattention":
            return "2.1.1" if sage_v2 else "1.0.6"
        return real_version(name)

    monkeypatch.setattr(torch.cuda, "is_available", is_available)
    monkeypatch.setattr(torch.cuda, "device_count", device_count)
    monkeypatch.setattr(torch.cuda, "get_device_capability", get_device_capability)
    monkeypatch.setattr(torch.cuda, "get_device_name", get_device_name)
    monkeypatch.setattr(
        torch.cuda,
        "get_device_properties",
        lambda index=None: types.SimpleNamespace(total_memory=memory[index] if isinstance(memory, (list, tuple)) else memory),
    )
    monkeypatch.setattr(torch.version, "hip", hip, raising=False)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: state.current)
    monkeypatch.setattr(torch.version, "cuda", runtime, raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(importlib.metadata, "version", version)
    return state


def test_no_cuda_yields_only_the_cpu_entry(monkeypatch):
    _environment(monkeypatch, devices=[], modules=PROBED)
    snapshot = capabilities()
    assert snapshot.devices == ()
    cpu = snapshot.device()
    assert cpu is snapshot.cpu
    assert (cpu.index, cpu.name, cpu.capability) == (None, "cpu", (0, 0))
    assert cpu.generation is GpuGeneration.NONE
    assert cpu.attention_backends == frozenset({SDPA})
    for spec in (0, 1, "cuda", "cuda:1", torch.device("cuda:0")):
        assert snapshot.device(spec) is cpu


def test_one_blackwell_device(monkeypatch):
    _environment(monkeypatch, devices=[(12, 0)], modules=PROBED)
    snapshot = capabilities()
    assert len(snapshot.devices) == 1
    gpu = snapshot.device(0)
    assert (gpu.index, gpu.name, gpu.capability) == (0, "GPU 0", (12, 0))
    assert gpu.generation is GpuGeneration.BLACKWELL
    assert snapshot.device() is gpu


def test_two_devices_keep_their_own_capability_and_backends(monkeypatch):
    _environment(monkeypatch, devices=[(8, 9), (12, 0)], modules=PROBED)
    first, second = capabilities().devices
    assert (first.index, first.capability, first.generation) == (0, (8, 9), GpuGeneration.ADA)
    assert (second.index, second.capability, second.generation) == (1, (12, 0), GpuGeneration.BLACKWELL)
    assert first.attention_backends == frozenset({SDPA, FLASH, SAGE, SAGE2, SPARGE})
    assert second.attention_backends == frozenset({SDPA, FLASH, SAGE, SAGE2, SAGE3})


def test_capability_error_becomes_zero_capability_for_that_device(monkeypatch):
    _environment(
        monkeypatch,
        devices=[RuntimeError("driver hiccup"), (8, 6)],
        modules={"flash_attn", "sageattention", "triton"},
    )
    broken, healthy = capabilities().devices
    assert (broken.index, broken.capability) == (0, (0, 0))
    assert broken.generation is GpuGeneration.NONE
    assert healthy.capability == (8, 6)


@pytest.mark.parametrize("broken", ["is_available", "device_count"])
def test_unreadable_device_list_degrades_to_cpu_only(monkeypatch, broken):
    _environment(monkeypatch, devices=[(9, 0)], modules=PROBED)
    monkeypatch.setattr(torch.cuda, broken, _raise)
    snapshot = capabilities()
    assert snapshot.devices == ()
    assert snapshot.device(0) is snapshot.cpu


def test_unreadable_device_name_keeps_the_device(monkeypatch):
    _environment(monkeypatch, devices=[(9, 0)], modules=PROBED)
    monkeypatch.setattr(torch.cuda, "get_device_name", _raise)
    gpu = capabilities().device(0)
    assert gpu.name is None
    assert gpu.capability == (9, 0)


@pytest.mark.parametrize(
    "spec, expected_index",
    [
        pytest.param(0, 0, id="int-0"),
        pytest.param(1, 1, id="int-1"),
        pytest.param("cuda:0", 0, id="str-cuda-0"),
        pytest.param("cuda:1", 1, id="str-cuda-1"),
        pytest.param(torch.device("cuda:0"), 0, id="device-cuda-0"),
        pytest.param(torch.device("cuda:1"), 1, id="device-cuda-1"),
    ],
)
def test_explicit_device_specs_resolve_to_their_entry(monkeypatch, spec, expected_index):
    _environment(monkeypatch, devices=[(8, 6), (12, 0)], modules=set(), current=1 - expected_index)
    snapshot = capabilities()
    assert snapshot.device(spec) is snapshot.devices[expected_index]


def test_none_and_bare_cuda_follow_the_current_device_at_call_time(monkeypatch):
    state = _environment(monkeypatch, devices=[(8, 6), (12, 0)], modules=set(), current=0)
    snapshot = capabilities()
    first, second = snapshot.devices
    for spec in (None, "cuda", torch.device("cuda")):
        assert snapshot.device(spec) is first
    state.current = 1
    for spec in (None, "cuda", torch.device("cuda")):
        assert snapshot.device(spec) is second
    assert capabilities() is snapshot


def test_unreadable_current_device_gets_the_cpu_entry(monkeypatch):
    _environment(monkeypatch, devices=[(8, 6)], modules=set())
    snapshot = capabilities()
    monkeypatch.setattr(torch.cuda, "current_device", _raise)
    assert snapshot.device() is snapshot.cpu
    assert snapshot.device(0) is snapshot.devices[0]


@pytest.mark.parametrize(
    "spec",
    [
        pytest.param(2, id="int-past-the-end"),
        pytest.param(-1, id="int-negative"),
        pytest.param("cuda:2", id="str-past-the-end"),
        pytest.param(torch.device("cuda:9"), id="device-past-the-end"),
        pytest.param("cpu", id="str-cpu"),
        pytest.param(torch.device("cpu"), id="device-cpu"),
        pytest.param(torch.device("meta"), id="device-meta"),
    ],
)
def test_out_of_range_and_non_cuda_specs_get_the_cpu_entry(monkeypatch, spec):
    _environment(monkeypatch, devices=[(8, 6), (12, 0)], modules=set())
    snapshot = capabilities()
    assert snapshot.device(spec) is snapshot.cpu


@pytest.mark.parametrize(
    "capability, generation",
    [
        ((0, 0), GpuGeneration.NONE),
        ((6, 1), GpuGeneration.PRE_AMPERE),
        ((7, 5), GpuGeneration.PRE_AMPERE),
        ((8, 0), GpuGeneration.AMPERE),
        ((8, 6), GpuGeneration.AMPERE),
        ((8, 7), GpuGeneration.AMPERE),
        ((8, 9), GpuGeneration.ADA),
        ((9, 0), GpuGeneration.HOPPER),
        ((9, 5), GpuGeneration.HOPPER),
        ((10, 0), GpuGeneration.BLACKWELL),
        ((10, 3), GpuGeneration.BLACKWELL),
        ((11, 0), GpuGeneration.BLACKWELL),
        ((12, 0), GpuGeneration.BLACKWELL),
        ((12, 1), GpuGeneration.BLACKWELL),
        ((13, 0), GpuGeneration.UNKNOWN),
    ],
)
def test_generation_follows_the_compute_capability(capability, generation):
    assert fake_device(capability).generation is generation


@pytest.mark.parametrize(
    "capability, major, minor, expected",
    [
        ((8, 6), 8, 0, True),
        ((8, 6), 8, 6, True),
        ((8, 6), 8, 7, False),
        ((8, 6), 9, 0, False),
        ((8, 6), 7, 9, True),
        ((8, 6), 0, 0, True),
        ((0, 0), 1, 0, False),
        ((12, 0), 12, 0, True),
        ((12, 0), 12, 1, False),
    ],
)
def test_at_least_compares_major_then_minor(capability, major, minor, expected):
    assert fake_device(capability).at_least(major, minor) is expected


def test_at_least_defaults_the_minor_to_zero():
    assert fake_device((8, 0)).at_least(8) is True
    assert fake_device((7, 5)).at_least(8) is False


def test_snapshot_carries_total_memory_per_device(monkeypatch):
    _environment(monkeypatch, devices=[(8, 9), (12, 0)], memory=[24 * 1024 ** 3, 32 * 1024 ** 3])
    first, second = capabilities().devices
    assert (first.total_memory, second.total_memory) == (24 * 1024 ** 3, 32 * 1024 ** 3)
    assert capabilities().cpu.total_memory == 0


def test_snapshot_vendor_is_cuda_without_hip(monkeypatch):
    _environment(monkeypatch, devices=[(8, 9)])
    assert capabilities().device(0).vendor == "cuda"
    assert capabilities().cpu.vendor == "none"


def test_snapshot_vendor_is_hip_on_a_rocm_build(monkeypatch):
    _environment(monkeypatch, devices=[(9, 0), (9, 0)], hip="6.2.41")
    assert [device.vendor for device in capabilities().devices] == ["hip", "hip"]


def test_snapshot_memory_probe_failure_falls_back_to_zero(monkeypatch):
    _environment(monkeypatch, devices=[(8, 9)])
    monkeypatch.setattr(torch.cuda, "get_device_properties", _raise)
    device = capabilities().device(0)
    assert device.total_memory == 0
    assert device.capability == (8, 9)


def test_snapshot_hip_probe_failure_falls_back_to_cuda(monkeypatch):
    _environment(monkeypatch, devices=[(8, 9)])

    class Broken:
        cuda = "12.8"

        def __getattr__(self, name):
            raise RuntimeError("no version")

    monkeypatch.setattr(torch, "version", Broken())
    assert capabilities().device(0).vendor == "cuda"


def test_fake_device_defaults_memory_and_vendor():
    device = fake_device((8, 6))
    assert (device.total_memory, device.vendor) == (24 * 1024 ** 3, "cuda")
    assert fake_capabilities(device).cpu.vendor == "none"


def test_snapshot_exposes_immutable_containers(monkeypatch):
    _environment(monkeypatch, devices=[(12, 0)], modules=PROBED)
    snapshot = capabilities()
    assert isinstance(snapshot.devices, tuple)
    assert isinstance(snapshot.devices[0].attention_backends, frozenset)


def test_snapshot_is_built_lazily_and_only_once(monkeypatch):
    state = _environment(monkeypatch, devices=[(12, 0), (8, 6)], modules=PROBED)
    assert state.device_count_calls == 0
    first = capabilities()
    second = capabilities()
    first.device()
    first.device(1)
    assert first is second
    assert state.device_count_calls == 1


def test_importing_the_module_does_not_probe_cuda(monkeypatch):
    state = _environment(monkeypatch, devices=[(12, 0)], modules=PROBED)
    name = "kernels_hardware_import_probe"
    spec = importlib.util.spec_from_file_location(name, inspect.getsourcefile(hardware))
    fresh = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, fresh)
    spec.loader.exec_module(fresh)
    assert (state.availability_calls, state.device_count_calls) == (0, 0)
    assert fresh.capabilities().devices[0].capability == (12, 0)
    assert state.device_count_calls == 1


ALL = PROBED
V2 = {"sageattention", "triton"}

ATTENTION_CASES = [
    pytest.param((7, 5), V2, False, "12.8", {SDPA, SAGE}, id="sage-v1-on-turing"),
    pytest.param((7, 5), V2, True, "12.8", {SDPA, SAGE}, id="sage2-build-needs-sm80"),
    pytest.param((8, 0), V2, True, "12.8", {SDPA, SAGE, SAGE2}, id="sage2-on-ampere"),
    pytest.param((8, 0), V2, False, "12.8", {SDPA, SAGE}, id="sage-v1-on-ampere"),
    pytest.param((8, 6), {"sageattention"}, True, "12.8", {SDPA}, id="sage-needs-triton"),
    pytest.param((6, 1), V2, True, "12.8", {SDPA}, id="sage-needs-sm70"),
    pytest.param((8, 6), {"flash_attn"}, True, "12.8", {SDPA, FLASH}, id="flash-only"),
    pytest.param((6, 1), {"flash_attn"}, True, "12.8", {SDPA, FLASH}, id="flash-has-no-sm-gate"),
    pytest.param((12, 0), {"sageattn3"}, True, "12.8", {SDPA, SAGE3}, id="sage3-sm120"),
    pytest.param((12, 1), {"sageattn3"}, True, "12.8", {SDPA, SAGE3}, id="sage3-sm121"),
    pytest.param((10, 0), {"sageattn3"}, True, "12.8", {SDPA, SAGE3}, id="sage3-sm100"),
    pytest.param((10, 3), {"sageattn3"}, True, "12.8", {SDPA}, id="sage3-exact-membership-sm103"),
    pytest.param((12, 2), {"sageattn3"}, True, "12.8", {SDPA}, id="sage3-exact-membership-sm122"),
    pytest.param((9, 0), {"sageattn3"}, True, "12.8", {SDPA}, id="sage3-not-on-hopper"),
    pytest.param((11, 0), {"sageattn3"}, True, "12.8", {SDPA}, id="sage3-not-on-sm110"),
    pytest.param((13, 0), {"sageattn3"}, True, "12.8", {SDPA}, id="sage3-not-on-future-majors"),
    pytest.param((12, 0), {"sageattn3"}, True, "12.7", {SDPA}, id="sage3-old-cuda-runtime"),
    pytest.param((12, 0), {"sageattn3"}, True, "12", {SDPA}, id="sage3-bare-major-runtime"),
    pytest.param((12, 0), {"sageattn3"}, True, "13.0", {SDPA, SAGE3}, id="sage3-newer-runtime"),
    pytest.param((12, 0), {"sageattn3"}, True, None, {SDPA}, id="sage3-cpu-torch-build"),
    pytest.param((12, 0), {"sageattn3"}, True, "garbage", {SDPA}, id="sage3-unparsable-runtime"),
    pytest.param((8, 0), {"spas_sage_attn"}, True, "12.8", {SDPA, SPARGE}, id="sparge-sm80"),
    pytest.param((8, 9), {"spas_sage_attn"}, True, "12.8", {SDPA, SPARGE}, id="sparge-sm89"),
    pytest.param((9, 0), {"spas_sage_attn"}, True, "12.8", {SDPA, SPARGE}, id="sparge-sm90"),
    pytest.param((6, 1), {"spas_sage_attn"}, True, "12.8", {SDPA}, id="sparge-not-on-pascal"),
    pytest.param((7, 5), {"spas_sage_attn"}, True, "12.8", {SDPA}, id="sparge-not-on-turing"),
    pytest.param((10, 0), {"spas_sage_attn"}, True, "12.8", {SDPA}, id="sparge-not-on-datacenter-blackwell"),
    pytest.param((12, 0), {"spas_sage_attn"}, True, "12.8", {SDPA}, id="sparge-not-on-blackwell"),
    pytest.param((8, 6), ALL, True, "12.8", {SDPA, FLASH, SAGE, SAGE2, SPARGE}, id="everything-on-ampere"),
    pytest.param((12, 0), ALL, True, "12.8", {SDPA, FLASH, SAGE, SAGE2, SAGE3}, id="everything-on-blackwell"),
    pytest.param((9, 0), set(), True, "12.8", {SDPA}, id="nothing-installed"),
    pytest.param(
        RuntimeError("driver hiccup"), {"flash_attn", "sageattention", "triton", "spas_sage_attn"},
        True, "12.8", {SDPA, FLASH}, id="unreadable-capability",
    ),
]


@pytest.mark.parametrize("capability, modules, sage_v2, runtime, expected", ATTENTION_CASES)
def test_attention_backends_follow_the_dispatcher_gates(
    monkeypatch, capability, modules, sage_v2, runtime, expected
):
    _environment(monkeypatch, devices=[capability], modules=modules, sage_v2=sage_v2, runtime=runtime)
    backends = capabilities().device(0).attention_backends
    assert backends == frozenset(expected)


def test_missing_sageattention_metadata_keeps_sage_without_sage2(monkeypatch):
    _environment(monkeypatch, devices=[(8, 6)], modules=V2)

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert capabilities().device(0).attention_backends == frozenset({SDPA, SAGE})


@pytest.mark.parametrize("error", [ValueError, ImportError])
def test_unprobeable_module_is_absent_and_nothing_raises(monkeypatch, error):
    _environment(monkeypatch, devices=[(8, 6)], modules=PROBED)
    working = importlib.util.find_spec

    def find_spec(name, package=None):
        if name == "flash_attn":
            raise error(name)
        return working(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    assert capabilities().device(0).attention_backends == frozenset({SDPA, SAGE, SAGE2, SPARGE})


def test_installed_capabilities_swaps_the_snapshot_and_restores_the_real_one(monkeypatch):
    _environment(monkeypatch, devices=[(8, 6)], modules=set())
    real = capabilities()
    fake = fake_capabilities(blackwell(), hopper())
    with installed_capabilities(fake) as installed:
        assert installed is fake
        assert capabilities() is fake
        assert capabilities().device(1).capability == (9, 0)
    assert capabilities() is real


def test_cpu_only_fake_has_no_cuda_devices(install_capabilities):
    fake = install_capabilities(cpu_only())
    assert capabilities() is fake
    assert fake.devices == ()
    assert fake.device("cuda:0") is fake.cpu


def test_fake_presets_carry_their_generation():
    assert ampere().generation is GpuGeneration.AMPERE
    assert ada().generation is GpuGeneration.ADA
    assert hopper().generation is GpuGeneration.HOPPER
    assert blackwell().generation is GpuGeneration.BLACKWELL


def test_refresh_picks_up_a_backend_installed_after_the_first_build(monkeypatch):
    modules = set()
    state = _environment(monkeypatch, devices=[(8, 6)], modules=modules)
    first = capabilities()
    assert FLASH not in first.device(0).attention_backends
    modules.add("flash_attn")
    assert capabilities() is first
    assert FLASH not in capabilities().device(0).attention_backends
    refreshed = refresh_capabilities()
    assert refreshed is not first
    assert refreshed.device(0).attention_backends == frozenset({SDPA, FLASH})
    assert capabilities() is refreshed
    assert FLASH not in first.device(0).attention_backends
    assert state.device_count_calls == 2


def test_refresh_before_any_build_builds_and_caches(monkeypatch):
    state = _environment(monkeypatch, devices=[(12, 0)], modules=PROBED)
    refreshed = refresh_capabilities()
    assert refreshed.device(0).capability == (12, 0)
    assert capabilities() is refreshed
    assert state.device_count_calls == 1


def test_refresh_replaces_an_installed_fake_with_a_rebuilt_snapshot_until_the_seam_restores(monkeypatch):
    _environment(monkeypatch, devices=[(8, 6)], modules=set())
    real = capabilities()
    fake = fake_capabilities(blackwell())
    with installed_capabilities(fake):
        assert capabilities() is fake
        rebuilt = refresh_capabilities()
        assert rebuilt is not fake
        assert rebuilt.device(0).capability == (8, 6)
        assert capabilities() is rebuilt
    assert capabilities() is real


def test_fake_capabilities_index_devices_by_position():
    fake = fake_capabilities(blackwell(), hopper(), ada())
    assert [device.index for device in fake.devices] == [0, 1, 2]
    assert fake.device(2).capability == (8, 9)


def test_refresh_invalidates_import_caches_before_rebuilding(monkeypatch):
    _environment(monkeypatch, devices=[(8, 6)], modules=set())
    events = []
    counted = torch.cuda.device_count

    def device_count():
        events.append("build")
        return counted()

    monkeypatch.setattr(torch.cuda, "device_count", device_count)
    monkeypatch.setattr(importlib, "invalidate_caches", lambda: events.append("invalidate"))
    capabilities()
    assert events == ["build"]
    refresh_capabilities()
    assert events == ["build", "invalidate", "build"]


def test_concurrent_first_calls_build_once_and_share_the_snapshot(monkeypatch):
    threads = 8
    state = _environment(monkeypatch, devices=[(12, 0)], modules=PROBED)
    counted = torch.cuda.device_count

    def slow_device_count():
        time.sleep(0.05)
        return counted()

    monkeypatch.setattr(torch.cuda, "device_count", slow_device_count)
    barrier = threading.Barrier(threads, timeout=10)
    results = []

    def worker():
        barrier.wait()
        results.append(capabilities())

    pool = [threading.Thread(target=worker) for _ in range(threads)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join(timeout=10)
    assert len(results) == threads
    assert all(result is results[0] for result in results)
    assert state.device_count_calls == 1
