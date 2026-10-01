import asyncio
import re
from typing import Any, Dict, Iterable, List, Optional

from src.features.backends.backend_config import NATIVE_ENGINE, NATIVE_REMOTE_DRIVER
from src.features.cloud.contracts import CLOUD_ENGINE
from src.features.generation.status_tracker import GenerationState
from src.platform.observability.logger import logger

BACKEND_TIMEOUT_SECONDS = 4.0
COMFYUI_ENGINE = "comfyui"

KIND_LOCAL = "local"
KIND_COMFYUI = "comfyui"
KIND_WORKER = "worker"
KIND_CLOUD = "cloud"
KIND_OTHER = "other"

STATUS_BY_HEALTH = {
    "healthy": "online",
    "available": "online",
    "busy": "online",
    "degraded": "degraded",
    "offline": "offline",
    "inactive": "inactive",
}

_GB = 1024 ** 3
_MB = 1024


def backend_kind(engine: str, driver: str) -> str:
    if engine == CLOUD_ENGINE:
        return KIND_CLOUD
    if engine == COMFYUI_ENGINE:
        return KIND_COMFYUI
    if engine == NATIVE_ENGINE:
        return KIND_WORKER if driver == NATIVE_REMOTE_DRIVER else KIND_LOCAL
    return KIND_OTHER


def _gb(value: Optional[float], unit: int) -> Optional[float]:
    if value is None:
        return None
    return round(float(value) / unit, 2)


def _percent(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if used is None or not total:
        return None
    return round(used / total * 100, 1)


_ACCELERATOR_PREFIX = re.compile(r"^(?:cuda|xpu):\d+\s+")


def clean_device_name(name: str) -> str:
    return _ACCELERATOR_PREFIX.sub("", name).split(" : ", 1)[0].strip()


def _gpu(
    index: int,
    name: str,
    total_gb: Optional[float],
    used_gb: Optional[float],
    temperature_c: Optional[float] = None,
    utilization_percent: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "vram_total_gb": total_gb,
        "vram_used_gb": used_gb,
        "vram_usage_percent": _percent(used_gb, total_gb),
        "temperature_c": temperature_c,
        "utilization_percent": utilization_percent,
    }


def _ram(total_gb: Optional[float], used_gb: Optional[float]) -> Optional[Dict[str, Any]]:
    if not total_gb:
        return None
    return {"total_gb": total_gb, "used_gb": used_gb, "usage_percent": _percent(used_gb, total_gb)}


def hardware_from_host_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
    cpu = stats.get("cpu") or {}
    ram = stats.get("ram") or {}
    gpu = stats.get("gpu") or {}
    gpus = []
    if gpu.get("available"):
        devices = gpu.get("devices") or [{**gpu, "index": 0, "name": ""}]
        for device in devices:
            gpus.append(_gpu(
                device.get("index", 0),
                device.get("name") or "GPU",
                _gb(device.get("vram_total"), _MB),
                _gb(device.get("vram_used"), _MB),
                device.get("temperature"),
                device.get("utilization_percent"),
            ))
    return {
        "cpu": {"usage_percent": cpu.get("usage_percent"), "cores": cpu.get("core_count")} if cpu.get("available", True) and cpu else None,
        "ram": _ram(_gb(ram.get("total"), _MB), _gb(ram.get("used"), _MB)) if ram.get("available", True) else None,
        "gpus": gpus,
    }


def hardware_from_comfyui(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    stats = info.get("system_stats") or {}
    system = stats.get("system") or {}
    gpus = []
    for index, device in enumerate(stats.get("devices") or []):
        if str(device.get("type", "")).lower() == "cpu":
            continue
        total = device.get("vram_total")
        free = device.get("vram_free")
        gpus.append(_gpu(
            index,
            clean_device_name(str(device.get("name") or "GPU")),
            _gb(total, _GB),
            _gb(total - free, _GB) if total is not None and free is not None else None,
        ))
    ram_total = system.get("ram_total")
    ram_free = system.get("ram_free")
    ram = _ram(
        _gb(ram_total, _GB),
        _gb(ram_total - ram_free, _GB) if ram_total is not None and ram_free is not None else None,
    )
    if not gpus and ram is None:
        return None
    return {"cpu": None, "ram": ram, "gpus": gpus}


def hardware_from_worker(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not info.get("connected"):
        return None
    gpus = []
    for gpu in info.get("gpus") or []:
        total = gpu.get("total_memory_bytes")
        free = gpu.get("free_memory_bytes")
        gpus.append(_gpu(
            gpu.get("index", len(gpus)),
            str(gpu.get("name") or "GPU"),
            _gb(total, _GB),
            _gb(total - free, _GB) if total is not None and free is not None else None,
        ))
    cpu_count = info.get("cpu_count")
    return {
        "cpu": {"usage_percent": None, "cores": cpu_count} if cpu_count else None,
        "ram": _ram(_gb(info.get("total_memory_bytes"), _GB), None),
        "gpus": gpus,
    }


def job_counts(tracker, backend_id: str) -> Dict[str, int]:
    running = queued = 0
    for record in tracker.list_active():
        if record.backend_id != backend_id:
            continue
        if record.state == GenerationState.RUNNING:
            running += 1
        elif record.state == GenerationState.PENDING:
            queued += 1
    return {"running": running, "queued": queued}


def _status_and_detail(health: Dict[str, Any]) -> tuple:
    raw = str(health.get("status", "error"))
    status = STATUS_BY_HEALTH.get(raw, "error")
    detail = health.get("reason") or health.get("error") or health.get("message")
    return status, detail


async def _probe(backend, kind: str, host_stats: Dict[str, Any]) -> Dict[str, Any]:
    if kind == KIND_COMFYUI:
        info = await backend.get_system_info()
        if not info.get("connected"):
            return {"status": "offline", "detail": info.get("error"), "hardware": None}
        return {"status": "online", "detail": None, "hardware": hardware_from_comfyui(info)}

    if kind == KIND_WORKER:
        health, info = await asyncio.gather(backend.health_check(), backend.get_system_info())
        status, detail = _status_and_detail(health)
        return {"status": status, "detail": detail, "hardware": hardware_from_worker(info)}

    health = await backend.health_check()
    status, detail = _status_and_detail(health)
    hardware = hardware_from_host_stats(host_stats) if kind == KIND_LOCAL else None
    return {"status": status, "detail": detail, "hardware": hardware}


async def _snapshot_one(config, backend, tracker, host_stats: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    driver = config.driver or config.engine
    kind = backend_kind(config.engine, driver)
    snapshot: Dict[str, Any] = {
        "id": config.id,
        "name": config.name,
        "engine": config.engine,
        "driver": driver,
        "kind": kind,
        "status": "inactive",
        "detail": "The backend is not running.",
        "hardware": None,
        "jobs": {**job_counts(tracker, config.id), "capacity": None},
    }
    if backend is None:
        return snapshot

    capacity = getattr(backend, "max_concurrent_runs", 1)
    snapshot["jobs"]["capacity"] = capacity if isinstance(capacity, int) else 1
    try:
        snapshot.update(await asyncio.wait_for(_probe(backend, kind, host_stats), timeout=timeout))
    except asyncio.TimeoutError:
        snapshot.update(status="unreachable", detail="No answer in time.", hardware=None)
    except Exception as error:
        logger.warning(f"[SYSTEM_MONITOR] Snapshot of backend {config.id} failed: {type(error).__name__}")
        snapshot.update(status="error", detail="The backend could not be read.", hardware=None)
    return snapshot


async def collect_backend_snapshots(
    configs: Iterable[Any],
    registry,
    tracker,
    host_stats: Dict[str, Any],
    timeout: float = BACKEND_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    return list(await asyncio.gather(*[
        _snapshot_one(config, registry.get_backend(config.id), tracker, host_stats, timeout)
        for config in configs
    ]))
