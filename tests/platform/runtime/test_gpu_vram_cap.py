"""POTIONUI_VRAM_CAP_GB applied at the source: GpuManager._get_memory_info()
(src.platform.runtime.gpu) — the read every GpuManager getter (get_total_vram,
get_free_vram, get_available_vram, ...) and, via `gpu_manager.get_vram_budget()`,
ModelLifecycleManager's admission decisions, all flow through.
"""
from collections import namedtuple
from threading import Lock

import pytest

from src.platform.runtime import gpu as gpu_module
from src.platform.runtime import vram_cap

_FakeNvmlMemory = namedtuple("_FakeNvmlMemory", ["total", "free", "used"])
_GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.delenv(vram_cap.VRAM_CAP_ENV_VAR, raising=False)
    vram_cap.reset_for_tests()
    yield
    vram_cap.reset_for_tests()


def _gpu_with_real_vram(monkeypatch, total_gb: float, free_gb: float) -> gpu_module.GpuManager:
    """A GpuManager whose NVML calls are faked out, bypassing __init__/nvmlInit
    entirely (mirrors test_gpu_budget.py's bypass pattern)."""
    g = gpu_module.GpuManager.__new__(gpu_module.GpuManager)
    g.handle = "fake-handle"
    g.available = True
    g.lock = Lock()
    g._vram_cap_gb = None
    used = int((total_gb - free_gb) * _GB)
    fake_info = _FakeNvmlMemory(total=int(total_gb * _GB), free=int(free_gb * _GB), used=used)
    monkeypatch.setattr(gpu_module, "nvmlDeviceGetMemoryInfo", lambda handle: fake_info)
    return g


class TestVramCapUnset:
    def test_total_and_free_are_the_real_hardware_values(self, monkeypatch):
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=28.0)
        assert g.get_total_vram() == int(32.0 * _GB / (1024 * 1024))
        assert g.get_free_vram() == int(28.0 * _GB / (1024 * 1024))


class TestVramCapSet:
    def test_total_and_free_are_capped(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=28.0)

        # Real card: 32GB total, 4GB used, 28GB free.
        # Capped to 16GB: total=16GB, used stays 4GB -> free=12GB.
        assert g.get_total_vram() == 16 * 1024  # MB
        assert g.get_free_vram() == 12 * 1024  # MB

    def test_used_vram_is_not_capped(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "16")
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=28.0)
        assert g.get_used_vram() == 4 * 1024  # MB, real usage untouched

    def test_available_vram_reflects_the_cap(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "8")
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=30.0)
        # get_available_vram() adds pytorch-reclaimable headroom on top of
        # free_gb; with CUDA unavailable in this test environment it falls
        # back to raw free_gb, which must already be capped.
        available = g.get_available_vram()
        assert available <= 8.0

    def test_cap_larger_than_the_real_card_has_no_effect(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "64")
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=28.0)
        assert g.get_total_vram() == 32 * 1024
        assert g.get_free_vram() == 28 * 1024

    def test_get_vram_budget_is_bounded_by_the_cap(self, monkeypatch):
        monkeypatch.setenv(vram_cap.VRAM_CAP_ENV_VAR, "8")
        g = _gpu_with_real_vram(monkeypatch, total_gb=32.0, free_gb=32.0)
        # Uncapped this would be ~27.2GB (32 * 0.85 safety margin); capped it
        # must never exceed the simulated 8GB card.
        assert g.get_vram_budget() <= 8.0
