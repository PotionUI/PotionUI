import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.features.generation.status_tracker import GenerationRecord, GenerationState
from src.features.system_monitor import routes
from src.features.system_monitor.backend_snapshots import collect_backend_snapshots
from src.features.system_monitor.coordinator import SystemMonitorCoordinator
from src.platform.security.current_user import get_current_active_user
from src.platform.security.user import AccountType

GB = 1024 ** 3

HOST = {
    "cpu": {"usage_percent": 12.5, "core_count": 16, "available": True},
    "ram": {"used": 16384, "free": 16384, "total": 32768, "usage_percent": 50, "available": True},
    "gpu": {
        "available": True,
        "vram_used": 20480, "vram_free": 4096, "vram_total": 24576, "vram_usage_percent": 83.3,
        "temperature": 60,
        "devices": [
            {"index": 0, "name": "RTX A", "vram_used": 1024, "vram_free": 23552, "vram_total": 24576,
             "vram_usage_percent": 4.2, "temperature": 40, "utilization_percent": 3},
            {"index": 1, "name": "RTX B", "vram_used": 20480, "vram_free": 4096, "vram_total": 24576,
             "vram_usage_percent": 83.3, "temperature": 60, "utilization_percent": 90},
        ],
    },
}


def _config(id, engine, driver="", name=None):
    return SimpleNamespace(id=id, name=name or id, engine=engine, driver=driver or engine)


class FakeBackend:
    def __init__(self, health=None, info=None, delay=0.0, capacity=1, boom=False):
        self._health = health or {"status": "healthy"}
        self._info = info or {}
        self.delay = delay
        self.max_concurrent_runs = capacity
        self.boom = boom
        self.cancelled = False

    async def _wait(self):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def health_check(self):
        await self._wait()
        if self.boom:
            raise RuntimeError("secret internal path /srv/x")
        return self._health

    async def get_system_info(self):
        await self._wait()
        return self._info


class FakeRegistry:
    def __init__(self, backends):
        self.backends = backends

    def get_backend(self, backend_id):
        return self.backends.get(backend_id)


class FakeTracker:
    def __init__(self, records=()):
        self.records = list(records)

    def list_active(self):
        return self.records


def _record(backend_id, state):
    return GenerationRecord(id=f"g-{backend_id}-{state.value}", backend_id=backend_id, state=state)


def _collect(configs, backends, records=(), timeout=0.2):
    return asyncio.run(collect_backend_snapshots(
        configs, FakeRegistry(backends), FakeTracker(records), HOST, timeout=timeout
    ))


def test_local_backend_lists_every_gpu_cpu_and_ram():
    (snap,) = _collect([_config("l", "native", "native.local")], {"l": FakeBackend(capacity=2)})
    assert snap["kind"] == "local" and snap["status"] == "online"
    gpus = snap["hardware"]["gpus"]
    assert [g["name"] for g in gpus] == ["RTX A", "RTX B"]
    assert gpus[1]["vram_total_gb"] == 24.0 and gpus[1]["vram_used_gb"] == 20.0
    assert gpus[1]["utilization_percent"] == 90
    assert snap["hardware"]["cpu"] == {"usage_percent": 12.5, "cores": 16}
    assert snap["hardware"]["ram"]["total_gb"] == 32.0 and snap["hardware"]["ram"]["usage_percent"] == 50.0
    assert snap["jobs"]["capacity"] == 2


def test_comfyui_backend_reads_vram_from_system_stats():
    info = {"connected": True, "system_stats": {
        "system": {"ram_total": 64 * GB, "ram_free": 32 * GB},
        "devices": [
            {"name": "cuda:0 RTX 4090 : cudaMallocAsync", "type": "cuda", "vram_total": 24 * GB, "vram_free": 6 * GB},
            {"name": "cpu", "type": "cpu", "vram_total": 0, "vram_free": 0},
        ],
    }}
    (snap,) = _collect([_config("c", "comfyui")], {"c": FakeBackend(info=info)})
    assert snap["kind"] == "comfyui" and snap["status"] == "online"
    assert [g["name"] for g in snap["hardware"]["gpus"]] == ["RTX 4090"]
    assert snap["hardware"]["gpus"][0]["vram_used_gb"] == 18.0
    assert snap["hardware"]["ram"]["used_gb"] == 32.0


def test_comfyui_offline_is_reported_with_its_error():
    info = {"connected": False, "error": "Connection timeout"}
    (snap,) = _collect([_config("c", "comfyui")], {"c": FakeBackend(info=info)})
    assert snap["status"] == "offline" and snap["detail"] == "Connection timeout"
    assert snap["hardware"] is None


def test_worker_reports_gpu_name_memory_and_busy_jobs():
    info = {"connected": True, "cpu_count": 8, "total_memory_bytes": 128 * GB, "gpus": [
        {"index": 0, "name": "H100", "total_memory_bytes": 80 * GB, "free_memory_bytes": 70 * GB},
        {"index": 1, "name": "H100", "total_memory_bytes": 80 * GB, "free_memory_bytes": None},
    ]}
    records = [_record("w", GenerationState.RUNNING), _record("w", GenerationState.PENDING), _record("w", GenerationState.PENDING), _record("other", GenerationState.RUNNING)]
    (snap,) = _collect(
        [_config("w", "native", "native.remote")], {"w": FakeBackend(info=info)}, records
    )
    assert snap["kind"] == "worker"
    assert snap["jobs"] == {"running": 1, "queued": 2, "capacity": 1}
    assert snap["hardware"]["gpus"][0]["vram_used_gb"] == 10.0
    assert snap["hardware"]["gpus"][1]["vram_used_gb"] is None
    assert snap["hardware"]["cpu"]["cores"] == 8


def test_worker_degraded_carries_the_reason():
    health = {"status": "degraded", "reason": "build differs"}
    info = {"connected": True, "gpus": []}
    (snap,) = _collect([_config("w", "native", "native.remote")], {"w": FakeBackend(health, info)})
    assert snap["status"] == "degraded" and snap["detail"] == "build differs"


def test_cloud_backend_has_status_and_jobs_but_no_hardware():
    records = [_record("k", GenerationState.RUNNING)]
    (snap,) = _collect([_config("k", "cloud", "openrouter")], {"k": FakeBackend()}, records)
    assert snap["kind"] == "cloud" and snap["status"] == "online"
    assert snap["hardware"] is None and snap["jobs"]["running"] == 1


def test_inactive_backend_is_reported_not_dropped():
    (snap,) = _collect([_config("x", "comfyui")], {})
    assert snap["status"] == "inactive" and snap["hardware"] is None


def test_slow_backend_is_unreachable_cancelled_and_does_not_stall_the_others():
    slow = FakeBackend(info={"connected": True}, delay=5)

    async def scenario():
        started = time.monotonic()
        snaps = await collect_backend_snapshots(
            [_config("slow", "comfyui"), _config("fast", "native", "native.local")],
            FakeRegistry({"slow": slow, "fast": FakeBackend()}),
            FakeTracker(),
            HOST,
            timeout=0.2,
        )
        return snaps, time.monotonic() - started, slow.cancelled

    snaps, elapsed, cancelled = asyncio.run(scenario())
    assert elapsed < 2
    by_id = {s["id"]: s for s in snaps}
    assert by_id["slow"]["status"] == "unreachable"
    assert by_id["fast"]["status"] == "online"
    assert cancelled


def test_backend_exception_is_an_error_without_leaking_the_message():
    (snap,) = _collect([_config("l", "native", "native.local")], {"l": FakeBackend(boom=True)})
    assert snap["status"] == "error"
    assert "secret" not in str(snap)


def _app(account_type):
    user = SimpleNamespace(id="u", account_type=account_type)

    class Coordinator:
        async def collect_system_stats(self):
            return HOST

    store = SimpleNamespace(get_enabled_backends=lambda: [_config("l", "native", "native.local")])
    registry = SimpleNamespace(
        backend_config_store=store, get_backend=lambda backend_id: FakeBackend()
    )
    container = SimpleNamespace(
        system_monitor_controller=routes.SystemMonitorController(None),
        system_monitor_coordinator=Coordinator(),
        backend_registry=registry,
        generation_status_tracker=FakeTracker(),
        plugin_repository=SimpleNamespace(
            get_plugin_by_id=lambda i: SimpleNamespace(enabled=True),
            get_plugin_setting=lambda *a: SimpleNamespace(setting_value="everyone"),
        ),
    )
    app = FastAPI()
    app.include_router(routes.build_router(container))
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app)


def test_aggregate_endpoint_is_admin_only_even_when_monitor_is_open_to_everyone():
    assert _app(AccountType.USER).get("/api/system/backends").status_code == 403


def test_aggregate_endpoint_returns_the_normalized_list_for_admin():
    body = _app(AccountType.ADMIN).get("/api/system/backends").json()
    assert body["success"] is True
    assert [b["id"] for b in body["data"]] == ["l"]
    assert len(body["data"][0]["hardware"]["gpus"]) == 2


class FakeProbeMonitor:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_system_snapshot(self):
        return self.snapshot


def test_coordinator_lists_every_gpu_and_summarises_the_busiest():
    snapshot = {
        "cpu": {"usage_percent": 1, "core_count": 4},
        "ram": {"total_gb": 8, "available_gb": 4, "used_gb": 4, "usage_percent": 50},
        "vram": {},
        "gpu": {"available": True},
        "gpus": [
            {"index": 0, "name": "A", "vram_total_gb": 24, "vram_used_gb": 1, "vram_free_gb": 23, "vram_usage_percent": 4.0, "temperature_c": 40, "utilization_percent": 1},
            {"index": 1, "name": "B", "vram_total_gb": 24, "vram_used_gb": 20, "vram_free_gb": 4, "vram_usage_percent": 83.0, "temperature_c": 70, "utilization_percent": 80},
        ],
    }
    gpu = SystemMonitorCoordinator(FakeProbeMonitor(snapshot)).get_system_stats()["gpu"]
    assert [d["name"] for d in gpu["devices"]] == ["A", "B"]
    assert gpu["vram_usage_percent"] == 83.0 and gpu["temperature"] == 70
    assert gpu["vram_total"] == 24 * 1024


def test_probe_reads_every_nvml_device(monkeypatch):
    from src.platform.observability import system_probe

    class Mem:
        def __init__(self, total, used):
            self.total, self.used, self.free = total, used, total - used

    mems = {"h0": Mem(24 * GB, 2 * GB), "h1": Mem(48 * GB, 24 * GB), "h2": Mem(0, 0)}
    monkeypatch.setattr(system_probe, "nvmlInit", lambda: None)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetCount", lambda: 3)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetHandleByIndex", lambda i: f"h{i}")
    monkeypatch.setattr(system_probe, "nvmlDeviceGetMemoryInfo", lambda h: mems[h])
    monkeypatch.setattr(system_probe, "nvmlDeviceGetName", lambda h: f"GPU-{h}".encode())
    monkeypatch.setattr(system_probe, "nvmlDeviceGetTemperature", lambda h, kind: 50)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetUtilizationRates", lambda h: SimpleNamespace(gpu=7))
    devices = system_probe.SystemMonitor().get_gpus_info()
    assert [d["name"] for d in devices] == ["GPU-h0", "GPU-h1", "GPU-h2"]
    assert devices[1]["vram_total_gb"] == 48.0 and devices[1]["vram_usage_percent"] == 50.0
    assert devices[2]["vram_usage_percent"] == 0.0


def test_probe_without_nvml_has_no_gpus(monkeypatch):
    from src.platform.observability import system_probe

    def fail():
        raise RuntimeError("no driver")

    monkeypatch.setattr(system_probe, "nvmlInit", fail)
    monitor = system_probe.SystemMonitor()
    assert monitor.get_gpus_info() == []
    assert monitor.get_system_snapshot()["gpus"] == []


def test_probe_skips_a_device_that_cannot_be_read(monkeypatch):
    from src.platform.observability import system_probe

    class Mem:
        total, used, free = 8 * GB, 2 * GB, 6 * GB

    def memory(handle):
        if handle == "h1":
            raise RuntimeError("lost")
        return Mem()

    monkeypatch.setattr(system_probe, "nvmlInit", lambda: None)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetCount", lambda: 3)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetHandleByIndex", lambda i: f"h{i}")
    monkeypatch.setattr(system_probe, "nvmlDeviceGetMemoryInfo", memory)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetName", lambda h: b"GPU")
    monkeypatch.setattr(system_probe, "nvmlDeviceGetTemperature", lambda h, kind: 50)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetUtilizationRates", lambda h: SimpleNamespace(gpu=1))
    assert [d["index"] for d in system_probe.SystemMonitor().get_gpus_info()] == [0, 2]


def test_snapshot_queries_nvml_memory_once_per_device(monkeypatch):
    from src.platform.observability import system_probe

    class Mem:
        total, used, free = 8 * GB, 2 * GB, 6 * GB

    calls = []
    monkeypatch.setattr(system_probe, "nvmlInit", lambda: None)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetCount", lambda: 2)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetHandleByIndex", lambda i: f"h{i}")
    monkeypatch.setattr(system_probe, "nvmlDeviceGetMemoryInfo", lambda h: calls.append(h) or Mem())
    monkeypatch.setattr(system_probe, "nvmlDeviceGetName", lambda h: b"GPU")
    monkeypatch.setattr(system_probe, "nvmlDeviceGetTemperature", lambda h, kind: 50)
    monkeypatch.setattr(system_probe, "nvmlDeviceGetUtilizationRates", lambda h: SimpleNamespace(gpu=1))
    monitor = system_probe.SystemMonitor()
    monkeypatch.setattr(monitor, "get_cpu_info", lambda: {})
    monkeypatch.setattr(monitor, "get_ram_info", lambda: {})
    snapshot = monitor.get_system_snapshot()
    assert sorted(calls) == ["h0", "h1"]
    assert snapshot["gpu"]["available"] is True and snapshot["vram"]["total_gb"] == 8.0


def test_coordinator_reports_no_gpu_without_devices_key():
    snapshot = {"cpu": {}, "ram": {}, "vram": {}, "gpu": {"available": False}, "gpus": []}
    gpu = SystemMonitorCoordinator(FakeProbeMonitor(snapshot)).get_system_stats()["gpu"]
    assert gpu == {"available": False}
