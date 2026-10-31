from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.gpu_generation import GpuGeneration
from src.platform.runtime.gpu_profile import (
    GENERATION_LABELS,
    GpuProfile,
    build_gpu_profile,
    detect_gpu_profile,
    fast_precisions_for,
    generation_for_capability,
    generations_supporting,
)
from tests.fixtures.kernel_capabilities import fake_device


@pytest.mark.parametrize(
    "capability,generation",
    [
        ((8, 0), "ampere"),
        ((8, 6), "ampere"),
        ((8, 7), "ampere"),
        ((8, 9), "ada"),
        ((9, 0), "hopper"),
        ((10, 0), "blackwell"),
        ((12, 0), "blackwell"),
        ((8, 8), "ampere"),
        ((9, 5), "hopper"),
        ((10, 3), "blackwell"),
        ((11, 0), "blackwell"),
        ((12, 1), "blackwell"),
        ((7, 5), "other"),
        ((6, 1), "other"),
        ((0, 0), "other"),
        ((13, 0), "other"),
        (None, "none"),
        ((), "none"),
    ],
)
def test_generation_for_capability(capability, generation):
    assert generation_for_capability(capability) == generation


@pytest.mark.parametrize(
    "capability,snapshot_generation",
    [((7, 5), GpuGeneration.PRE_AMPERE), ((13, 0), GpuGeneration.UNKNOWN)],
)
def test_snapshot_generations_outside_the_named_set_are_other(capability, snapshot_generation):
    device = fake_device(capability)
    profile = build_gpu_profile(device.capability, 24, device.name)

    assert device.generation is snapshot_generation
    assert profile.generation == "other"
    assert profile.generation_label == "this GPU"


@pytest.mark.parametrize("capability", [(7, 5), (8, 0), (8, 6), (8, 9), (9, 0), (10, 0), (12, 0), (13, 0)])
def test_nvfp4_support_tracks_the_snapshot_generation(capability):
    device = fake_device(capability)
    profile = build_gpu_profile(device.capability, 24, device.name)

    assert profile.supports("nvfp4") is (device.generation is GpuGeneration.BLACKWELL)


def test_generations_supporting_lists_the_gated_generations():
    assert generations_supporting("nvfp4") == ("blackwell",)
    assert GENERATION_LABELS["blackwell"] == "RTX 50-series (Blackwell)"
    for precision in ("bf16", "fp16", "fp8", "int8"):
        assert generations_supporting(precision) == ()


def test_fast_precisions_per_generation():
    assert set(fast_precisions_for("ampere", (8, 6))) == {"bf16", "fp16"}
    assert set(fast_precisions_for("ada", (8, 9))) == {"bf16", "fp16", "fp8"}
    assert set(fast_precisions_for("hopper", (9, 0))) == {"bf16", "fp16", "fp8"}
    assert set(fast_precisions_for("blackwell", (12, 0))) == {"bf16", "fp16", "fp8", "nvfp4"}
    assert fast_precisions_for("none", None) == ()
    assert not build_gpu_profile((8, 6), 24).is_fast("int8")


def test_other_vendors_get_no_accelerated_quantized_precisions():
    profile = build_gpu_profile((12, 0), 24, vendor_is_nvidia=False)
    assert profile.generation == "other"
    assert profile.fast_precisions == ("bf16", "fp16")


@pytest.mark.parametrize("capability,nvfp4_fast", [((10, 0), False), ((11, 0), False), ((12, 0), True)])
def test_nvfp4_is_fast_only_from_sm120(capability, nvfp4_fast):
    profile = build_gpu_profile(capability, 32)
    assert profile.generation == "blackwell"
    assert profile.is_fast("nvfp4") is nvfp4_fast
    assert profile.is_fast("fp8")


def test_nvfp4_is_supported_only_on_blackwell():
    assert build_gpu_profile((12, 0), 32).supports("nvfp4")
    assert not build_gpu_profile((8, 9), 24).supports("nvfp4")
    assert build_gpu_profile((8, 6), 24).supports("fp8")
    assert not GpuProfile().supports("bf16")


def test_profile_dict_shape():
    profile = build_gpu_profile((8, 9), 23.988, "NVIDIA GeForce RTX 4090")
    assert profile.to_dict() == {
        "generation": "ada",
        "generation_label": "RTX 40-series (Ada)",
        "name": "NVIDIA GeForce RTX 4090",
        "vram_gb": 24.0,
        "compute_capability": "8.9",
        "fast_precisions": ["bf16", "fp16", "fp8"],
    }
    assert profile.vram_class_gb == 24


def test_non_nvidia_device_is_other():
    profile = build_gpu_profile((9, 4), 192, "AMD Instinct", vendor_is_nvidia=False)
    assert profile.generation == "other"
    assert "fp8" not in profile.fast_precisions


def test_no_gpu_profile():
    assert GpuProfile().to_dict() == {
        "generation": "none",
        "generation_label": "no GPU",
        "name": None,
        "vram_gb": 0.0,
        "compute_capability": None,
        "fast_precisions": [],
    }


@pytest.fixture(autouse=True)
def _fresh_snapshot():
    from src.platform.runtime.native.kernels.hardware import _install_snapshot

    previous = _install_snapshot(None)
    yield
    _install_snapshot(previous)


def _fake_cuda(monkeypatch, *devices, hip=None):
    def properties(index):
        entry = devices[index]
        if isinstance(entry, Exception):
            raise entry
        return SimpleNamespace(
            major=entry[0], minor=entry[1], total_memory=entry[2], name=entry[3]
        )

    def capability(index=None):
        entry = devices[0 if index is None else index]
        if isinstance(entry, Exception):
            raise entry
        return (entry[0], entry[1])

    def name(index=None):
        entry = devices[0 if index is None else index]
        if isinstance(entry, Exception):
            raise entry
        return entry[3]

    monkeypatch.setattr(torch.cuda, "is_available", lambda: bool(devices))
    monkeypatch.setattr(torch.cuda, "device_count", lambda: len(devices))
    monkeypatch.setattr(torch.cuda, "get_device_properties", properties)
    monkeypatch.setattr(torch.cuda, "get_device_capability", capability)
    monkeypatch.setattr(torch.cuda, "get_device_name", name)
    monkeypatch.setattr(torch.version, "hip", hip, raising=False)


GIB = 1024 ** 3

DETECT_CASES = [
    pytest.param(
        (12, 0, 32 * GIB, "NVIDIA GeForce RTX 5090"),
        None,
        {
            "generation": "blackwell",
            "generation_label": "RTX 50-series (Blackwell)",
            "name": "NVIDIA GeForce RTX 5090",
            "vram_gb": 32.0,
            "compute_capability": "12.0",
            "fast_precisions": ["bf16", "fp16", "fp8", "nvfp4"],
        },
        id="blackwell",
    ),
    pytest.param(
        (8, 6, 24 * GIB, "NVIDIA GeForce RTX 3090"),
        None,
        {
            "generation": "ampere",
            "generation_label": "RTX 30-series (Ampere)",
            "name": "NVIDIA GeForce RTX 3090",
            "vram_gb": 24.0,
            "compute_capability": "8.6",
            "fast_precisions": ["bf16", "fp16"],
        },
        id="ampere",
    ),
    pytest.param(
        (8, 9, 25757220864, "NVIDIA GeForce RTX 4090"),
        None,
        {
            "generation": "ada",
            "generation_label": "RTX 40-series (Ada)",
            "name": "NVIDIA GeForce RTX 4090",
            "vram_gb": 24.0,
            "compute_capability": "8.9",
            "fast_precisions": ["bf16", "fp16", "fp8"],
        },
        id="ada-rounded",
    ),
    pytest.param(
        (9, 0, 80 * GIB, "NVIDIA H100"),
        None,
        {
            "generation": "hopper",
            "generation_label": "Hopper",
            "name": "NVIDIA H100",
            "vram_gb": 80.0,
            "compute_capability": "9.0",
            "fast_precisions": ["bf16", "fp16", "fp8"],
        },
        id="hopper",
    ),
    pytest.param(
        (7, 5, 8 * GIB, "NVIDIA GeForce RTX 2080"),
        None,
        {
            "generation": "other",
            "generation_label": "this GPU",
            "name": "NVIDIA GeForce RTX 2080",
            "vram_gb": 8.0,
            "compute_capability": "7.5",
            "fast_precisions": ["bf16", "fp16"],
        },
        id="turing",
    ),
    pytest.param(
        (9, 0, 24 * GIB, "AMD Radeon RX 7900 XTX"),
        "6.2.41",
        {
            "generation": "other",
            "generation_label": "this GPU",
            "name": "AMD Radeon RX 7900 XTX",
            "vram_gb": 24.0,
            "compute_capability": "9.0",
            "fast_precisions": ["bf16", "fp16"],
        },
        id="hip",
    ),
]


@pytest.mark.parametrize("device,hip,expected", DETECT_CASES)
def test_detect_profile_for_faked_hardware(monkeypatch, device, hip, expected):
    _fake_cuda(monkeypatch, device, hip=hip)
    assert detect_gpu_profile().to_dict() == expected


def test_detect_without_cuda_returns_none(monkeypatch):
    _fake_cuda(monkeypatch)
    assert detect_gpu_profile() == GpuProfile()


def test_detect_reads_the_requested_device(monkeypatch):
    _fake_cuda(
        monkeypatch,
        (8, 6, 24 * GIB, "first"),
        (12, 0, 32 * GIB, "second"),
    )
    assert detect_gpu_profile(1).name == "second"
    assert detect_gpu_profile(1).generation == "blackwell"
    assert detect_gpu_profile().name == "first"


def test_detect_unknown_device_index_returns_none(monkeypatch):
    _fake_cuda(monkeypatch, (8, 6, 24 * GIB, "only"))
    assert detect_gpu_profile(3) == GpuProfile()


def test_detect_survives_a_driver_error(monkeypatch):
    _fake_cuda(monkeypatch, RuntimeError("driver gone"))
    assert detect_gpu_profile() == GpuProfile()
