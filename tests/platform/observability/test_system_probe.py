"""
Tests for the SystemMonitor probe.

Covers the snapshot schema every consumer formats against and the CPU-only
fallback that a GPU-less host takes.
"""
import threading
from types import SimpleNamespace

import pytest

from src.platform.observability import system_probe
from src.platform.observability.system_probe import SystemMonitor


@pytest.fixture
def cpu_only_probe():
    """A probe built without NVML, as on a host with no usable GPU."""
    probe = SystemMonitor.__new__(SystemMonitor)
    probe.lock = threading.Lock()
    probe.gpu_available = False
    probe.gpu_handle = None
    probe.gpu_handles = []
    probe._failing_gpus = set()
    return probe


class TestSystemProbe:
    def test_cpu_info_shape(self, cpu_only_probe):
        cpu = cpu_only_probe.get_cpu_info()

        assert set(cpu) == {"usage_percent", "core_count", "core_count_physical"}
        assert 0.0 <= cpu["usage_percent"] <= 100.0
        assert cpu["core_count"] >= 1

    def test_cpu_sample_does_not_hold_the_probe_lock(self, cpu_only_probe):
        """
        The 100ms CPU wait must not block other threads reading RAM or GPU.
        """
        result = []

        def sample():
            result.append(cpu_only_probe.get_cpu_info())

        with cpu_only_probe.lock:
            worker = threading.Thread(target=sample)
            worker.start()
            worker.join(timeout=2.0)

        assert not worker.is_alive(), "get_cpu_info blocked on the probe lock"
        assert result and "usage_percent" in result[0]

    def test_snapshot_shape(self, cpu_only_probe):
        snapshot = cpu_only_probe.get_system_snapshot()

        assert set(snapshot) == {"cpu", "ram", "vram", "gpu", "gpus"}
        assert set(snapshot["cpu"]) == {"usage_percent", "core_count", "core_count_physical"}
        assert set(snapshot["ram"]) == {
            "total_gb", "available_gb", "used_gb", "usage_percent"
        }
        assert set(snapshot["vram"]) == {
            "total_gb", "available_gb", "used_gb", "free_gb",
            "reserved_gb", "allocated_gb", "usage_percent",
        }
        assert set(snapshot["gpu"]) == {
            "temperature_c", "utilization_percent", "name", "available"
        }

    def test_gpu_unavailable_fallback(self, cpu_only_probe):
        assert cpu_only_probe.get_gpu_info() == {
            "temperature_c": 0.0,
            "utilization_percent": 0.0,
            "name": "No GPU",
            "available": False,
        }
        assert cpu_only_probe.get_vram_info() == {
            "total_gb": 0.0,
            "available_gb": 0.0,
            "used_gb": 0.0,
            "free_gb": 0.0,
            "reserved_gb": 0.0,
            "allocated_gb": 0.0,
            "usage_percent": 0.0,
        }


GIB = 1024 ** 3


@pytest.fixture
def two_gpu_probe(monkeypatch):
    probe = SystemMonitor.__new__(SystemMonitor)
    probe.lock = threading.Lock()
    probe.gpu_available = True
    probe.gpu_handles = ["gpu-0", "gpu-1"]
    probe.gpu_handle = "gpu-0"
    probe._failing_gpus = set()
    probe.broken = set()
    monkeypatch.setattr(probe, "get_cpu_info", lambda: {})
    monkeypatch.setattr(probe, "get_ram_info", lambda: {})

    def memory(handle):
        if handle in probe.broken:
            raise RuntimeError(f"{handle} lost")
        total = 24 if handle == "gpu-0" else 8
        return SimpleNamespace(total=total * GIB, used=GIB, free=(total - 1) * GIB)

    monkeypatch.setattr(system_probe, "nvmlDeviceGetMemoryInfo", memory)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetName", lambda handle: handle.encode())
    monkeypatch.setattr(system_probe, "nvmlDeviceGetTemperature", lambda handle, kind: 50)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetUtilizationRates", lambda handle: SimpleNamespace(gpu=10))
    return probe


class TestPrimaryGpu:
    def test_snapshot_reports_device_zero_as_primary(self, two_gpu_probe):
        snapshot = two_gpu_probe.get_system_snapshot()

        assert snapshot["gpu"]["name"] == "gpu-0"
        assert snapshot["vram"]["total_gb"] == 24
        assert [g["index"] for g in snapshot["gpus"]] == [0, 1]

    def test_snapshot_does_not_promote_device_one_when_device_zero_is_unreadable(self, two_gpu_probe, monkeypatch):
        two_gpu_probe.broken.add("gpu-0")
        monkeypatch.setattr(two_gpu_probe, "get_vram_info", lambda: {"total_gb": 0.0})
        monkeypatch.setattr(two_gpu_probe, "get_gpu_info", lambda: {"name": "Error", "available": False})

        snapshot = two_gpu_probe.get_system_snapshot()

        assert snapshot["gpu"]["name"] == "Error"
        assert snapshot["vram"]["total_gb"] == 0.0
        assert [g["index"] for g in snapshot["gpus"]] == [1]


class TestFailingGpuLogging:
    def test_a_failing_device_is_logged_once_across_polls(self, two_gpu_probe, monkeypatch):
        errors = []
        monkeypatch.setattr(system_probe.logger, "error", errors.append)
        two_gpu_probe.broken.add("gpu-1")

        for _ in range(3):
            two_gpu_probe.get_gpus_info()

        assert len(errors) == 1
        assert "GPU 1" in errors[0]

    def test_a_recovered_device_is_logged_again_if_it_fails_later(self, two_gpu_probe, monkeypatch):
        errors = []
        monkeypatch.setattr(system_probe.logger, "error", errors.append)

        two_gpu_probe.broken.add("gpu-1")
        two_gpu_probe.get_gpus_info()
        two_gpu_probe.broken.clear()
        two_gpu_probe.get_gpus_info()
        two_gpu_probe.broken.add("gpu-1")
        two_gpu_probe.get_gpus_info()

        assert len(errors) == 2
