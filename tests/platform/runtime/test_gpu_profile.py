from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.gpu_profile import (
    GpuProfile,
    build_gpu_profile,
    detect_gpu_profile,
    fast_precisions_for,
    generation_for_capability,
)


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
        ((7, 5), "other"),
        (None, "none"),
    ],
)
def test_generation_for_capability(capability, generation):
    assert generation_for_capability(capability) == generation


def test_fast_precisions_per_generation():
    assert set(fast_precisions_for("ampere")) == {"bf16", "fp16", "int8"}
    assert set(fast_precisions_for("ada")) == {"bf16", "fp16", "int8", "fp8"}
    assert set(fast_precisions_for("hopper")) == {"bf16", "fp16", "int8", "fp8"}
    assert set(fast_precisions_for("blackwell")) == {"bf16", "fp16", "int8", "fp8", "nvfp4"}
    assert fast_precisions_for("none") == ()


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
        "fast_precisions": ["bf16", "fp16", "int8", "fp8"],
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


def test_detect_without_cuda_returns_none(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert detect_gpu_profile().generation == "none"


def test_detect_reads_device_properties(monkeypatch):
    props = SimpleNamespace(major=12, minor=0, total_memory=32 * 1024 ** 3, name="NVIDIA GeForce RTX 5090")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda index: props)
    monkeypatch.setattr(torch.version, "hip", None, raising=False)
    profile = detect_gpu_profile()
    assert profile.generation == "blackwell"
    assert profile.vram_gb == 32.0
    assert profile.name == "NVIDIA GeForce RTX 5090"


def test_detect_survives_a_driver_error(monkeypatch):
    def boom(index):
        raise RuntimeError("driver gone")

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_properties", boom)
    assert detect_gpu_profile() == GpuProfile()

