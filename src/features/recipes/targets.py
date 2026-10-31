from __future__ import annotations

import logging
import os
import shutil
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.features.recipes.executors._async_bridge import run_sync
from src.platform.runtime.gpu_profile import GpuProfile, build_gpu_profile, detect_gpu_profile

logger = logging.getLogger(__name__)

DISK_RESERVE_BYTES = 2 * 1024 ** 3
REMOTE_DRIVER = "native.remote"
LOCAL_KIND = "local"
REMOTE_KIND = "remote"
EXTERNAL_KIND = "external"
_DOWNLOADING_ENGINE = "native"
_REMOTE_CACHE_SECONDS = 60.0
_ESTIMATE_SAMPLES = 5
_MIN_SAMPLE_BYTES = 10 * 1024 ** 2
_MIN_SAMPLE_SECONDS = 1.0


@dataclass(frozen=True)
class TargetOption:
    backend_id: Optional[str]
    label: str
    kind: str
    driver: str = ""
    device: Optional[str] = None

    @property
    def key(self) -> str:
        return self.backend_id or LOCAL_KIND


@dataclass(frozen=True)
class DiskVolume:
    id: str
    label: str
    free_bytes: Optional[int]
    reserve_bytes: int
    model_types: Tuple[str, ...]

    @property
    def usable_bytes(self) -> Optional[int]:
        if self.free_bytes is None:
            return None
        return max(0, self.free_bytes - self.reserve_bytes)

    def to_dict(self, target: str) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target": target,
            "label": self.label,
            "free_bytes": self.free_bytes,
            "reserve_bytes": self.reserve_bytes,
            "model_types": list(self.model_types),
        }


@dataclass(frozen=True)
class TargetProfile:
    option: TargetOption
    gpu: GpuProfile
    disks: Tuple[DiskVolume, ...] = ()
    probed: bool = True

    @property
    def destination_backend_id(self) -> Optional[str]:
        return self.option.backend_id if self.option.kind == REMOTE_KIND else None

    @property
    def downloads(self) -> bool:
        return self.option.kind in (LOCAL_KIND, REMOTE_KIND)

    def disk_for(self, model_type: str) -> Optional[DiskVolume]:
        for disk in self.disks:
            if model_type in disk.model_types:
                return disk
        return self.disks[0] if len(self.disks) == 1 and self.option.kind == REMOTE_KIND else None

    @property
    def free_bytes(self) -> Optional[int]:
        frees = [disk.free_bytes for disk in self.disks if disk.free_bytes is not None]
        return max(frees) if frees else None


@dataclass
class _RemoteReading:
    gpu: GpuProfile
    free_disk_bytes: Optional[int]
    taken_at: float


def _device_index(device: Optional[str]) -> int:
    if isinstance(device, str) and device.startswith("cuda:"):
        try:
            return int(device.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


def _parse_capability(raw: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(raw, str) or "." not in raw:
        return None
    major, _, minor = raw.partition(".")
    try:
        return int(major), int(minor)
    except ValueError:
        return None


def remote_gpu_profile(info: Dict[str, Any]) -> GpuProfile:
    gpus = info.get("gpus") or []
    if not gpus:
        return GpuProfile()
    first = gpus[0]
    total = first.get("total_memory_bytes") or 0
    return build_gpu_profile(
        _parse_capability(first.get("compute_capability")),
        total / 1024 ** 3,
        name=first.get("name"),
        vendor_is_nvidia=first.get("compute_capability") is not None,
    )


class InstallTargets:
    def __init__(
        self,
        backend_registry: Any = None,
        model_roots: Any = None,
        gpu_profile_provider: Optional[Callable[..., GpuProfile]] = None,
        disk_usage: Callable[[Any], Any] = shutil.disk_usage,
        download_repository: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.backend_registry = backend_registry
        self.model_roots = model_roots
        self._gpu_profile_provider = gpu_profile_provider or detect_gpu_profile
        self._disk_usage = disk_usage
        self.download_repository = download_repository
        self._clock = clock
        self._remote: Dict[str, _RemoteReading] = {}

    def options(self, engine: str) -> List[TargetOption]:
        backends = []
        if self.backend_registry is not None:
            try:
                backends = list(self.backend_registry.get_backends_for_engine(engine))
            except Exception:
                logger.exception("Listing backends for engine '%s' failed", engine)
        options: List[TargetOption] = []
        for backend in backends:
            config = getattr(backend, "config", None)
            driver = getattr(config, "effective_driver", "") or engine
            name = getattr(backend, "name", None) or getattr(backend, "backend_id", "")
            if engine != _DOWNLOADING_ENGINE:
                kind, label = EXTERNAL_KIND, name
            elif driver == REMOTE_DRIVER:
                kind, label = REMOTE_KIND, f"Worker {name}"
            else:
                kind, label = LOCAL_KIND, "This computer"
            options.append(
                TargetOption(
                    backend_id=getattr(backend, "backend_id", None),
                    label=label,
                    kind=kind,
                    driver=driver,
                    device=getattr(config, "device", None),
                )
            )
        if not options and engine == _DOWNLOADING_ENGINE:
            options.append(TargetOption(backend_id=None, label="This computer", kind=LOCAL_KIND, driver=engine))
        return options

    def default_backend_id(self, engine: str) -> Optional[str]:
        store = getattr(self.backend_registry, "backend_config_store", None)
        if store is None:
            return None
        try:
            config = store.get_default_backend(engine)
        except Exception:
            return None
        return getattr(config, "id", None)

    def resolve(self, engine: str, wanted: Optional[str] = None) -> Tuple[Optional[TargetOption], List[TargetOption]]:
        options = self.options(engine)
        if not options:
            return None, options
        if wanted and wanted != "auto":
            for option in options:
                if option.key == wanted or option.backend_id == wanted:
                    return option, options
            return None, options
        default_id = self.default_backend_id(engine)
        for option in options:
            if option.backend_id == default_id:
                return option, options
        local = [option for option in options if option.kind == LOCAL_KIND]
        return (local or options)[0], options

    def profile(self, option: TargetOption, model_types: Iterable[str], probe_remote: bool = True) -> TargetProfile:
        if option.kind == LOCAL_KIND:
            return TargetProfile(option=option, gpu=self._local_gpu(option), disks=tuple(self._local_disks(model_types)))
        if option.kind == REMOTE_KIND:
            reading = self._remote_reading(option, probe_remote)
            if reading is None:
                return TargetProfile(option=option, gpu=GpuProfile(), disks=(), probed=False)
            disk = DiskVolume(
                id="d0",
                label=option.label,
                free_bytes=reading.free_disk_bytes,
                reserve_bytes=DISK_RESERVE_BYTES,
                model_types=tuple(sorted(set(model_types))),
            )
            return TargetProfile(option=option, gpu=reading.gpu, disks=(disk,))
        return TargetProfile(option=option, gpu=GpuProfile(), disks=())

    def _local_gpu(self, option: TargetOption) -> GpuProfile:
        try:
            return self._gpu_profile_provider(_device_index(option.device))
        except TypeError:
            return self._gpu_profile_provider()
        except Exception:
            logger.exception("Reading the local GPU profile failed")
            return GpuProfile()

    def _local_disks(self, model_types: Iterable[str]) -> List[DiskVolume]:
        if self.model_roots is None:
            return []
        volumes: Dict[Any, Dict[str, Any]] = {}
        for model_type in sorted(set(model_types)):
            try:
                path = Path(self.model_roots.write_dir(model_type).path)
            except Exception:
                continue
            probe = path
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            try:
                usage = self._disk_usage(probe)
            except OSError:
                continue
            try:
                key = os.stat(probe).st_dev
            except OSError:
                key = str(probe)
            entry = volumes.setdefault(key, {"free": usage.free, "types": []})
            entry["types"].append(model_type)
        disks = []
        for index, entry in enumerate(volumes.values()):
            disks.append(
                DiskVolume(
                    id=f"d{index}",
                    label="Models folder" if len(volumes) == 1 else f"Models folder {index + 1}",
                    free_bytes=int(entry["free"]),
                    reserve_bytes=DISK_RESERVE_BYTES,
                    model_types=tuple(entry["types"]),
                )
            )
        return disks

    def _remote_reading(self, option: TargetOption, probe: bool) -> Optional[_RemoteReading]:
        cached = self._remote.get(option.key)
        now = self._clock()
        if cached is not None and (not probe or now - cached.taken_at < _REMOTE_CACHE_SECONDS):
            return cached
        if not probe or self.backend_registry is None:
            return cached
        backend = self.backend_registry.get_backend(option.backend_id) if option.backend_id else None
        if backend is None:
            return cached
        try:
            info = run_sync(backend.get_system_info())
        except Exception:
            logger.exception("Reading worker '%s' for an install plan failed", option.backend_id)
            return cached
        if not isinstance(info, dict) or not info.get("connected", True):
            return cached
        free = info.get("free_disk_bytes")
        reading = _RemoteReading(
            gpu=remote_gpu_profile(info),
            free_disk_bytes=int(free) if isinstance(free, (int, float)) and free > 0 else None,
            taken_at=now,
        )
        self._remote[option.key] = reading
        return reading

    def estimate(self, option: Optional[TargetOption]) -> Optional[Dict[str, Any]]:
        repo = self.download_repository
        if repo is None:
            return None
        try:
            from src.features.downloads.models import DownloadStatus

            rows = repo.get_all(limit=40, status=DownloadStatus.COMPLETED, top_level_only=True)
        except Exception:
            logger.exception("Reading download history for an estimate failed")
            return None
        destination = option.backend_id if option is not None and option.kind == REMOTE_KIND else None
        speeds: List[float] = []
        for row in rows:
            if getattr(row, "destination_backend_id", None) != destination:
                continue
            started, completed = getattr(row, "started_at", None), getattr(row, "completed_at", None)
            size = getattr(row, "total_bytes", None) or getattr(row, "downloaded_bytes", None)
            if not started or not completed or not size or size < _MIN_SAMPLE_BYTES:
                continue
            seconds = (completed - started).total_seconds()
            if seconds < _MIN_SAMPLE_SECONDS:
                continue
            speeds.append(size / seconds)
            if len(speeds) >= _ESTIMATE_SAMPLES:
                break
        if not speeds:
            return None
        return {
            "bytes_per_sec": int(statistics.median(speeds)),
            "basis": (
                f"median of the last {len(speeds)} completed downloads"
                if len(speeds) > 1
                else "your last completed download"
            ),
        }
