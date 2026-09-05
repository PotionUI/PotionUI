"""
Tests for the SystemMonitor probe.

Covers the snapshot schema every consumer formats against and the CPU-only
fallback that a GPU-less host takes.
"""
import threading

import pytest

from src.platform.observability.system_probe import SystemMonitor


@pytest.fixture
def cpu_only_probe():
    """A probe built without NVML, as on a host with no usable GPU."""
    probe = SystemMonitor.__new__(SystemMonitor)
    probe.lock = threading.Lock()
    probe.gpu_available = False
    probe.gpu_handle = None
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

        assert set(snapshot) == {"cpu", "ram", "vram", "gpu"}
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
