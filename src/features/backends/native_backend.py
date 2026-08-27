from typing import Any, Dict, List

from src.platform.observability.logger import logger

from .in_process_backend import InProcessBackend
from .model_listing import BackendModel, deduplicate
from .native_model_scan import scan_native_models


def _cuda_device_index(device: str) -> int:
    """Parse the index out of a "cuda"/"cuda:N" device string. Bare "cuda" is index 0."""
    if ":" in device:
        try:
            return int(device.split(":", 1)[1])
        except ValueError:
            return 0
    return 0


class NativeBackend(InProcessBackend):
    """
    The built-in `native` engine: diffusers pipes executed in this process.

    Because its executor holds a single GPU and a single cancellation flag, the
    native engine has exactly one backend, provisioned automatically.
    """

    def supports_model_listing(self) -> bool:
        return True

    async def list_models(self) -> List[BackendModel]:
        """Walk this host's models directory.

        `models_dir` is a host-level setting, not a backend one: it names where this
        machine keeps weights. A ComfyUI server has its own models directory that
        PotionUI never reads.
        """
        from src.platform.settings.settings import Settings
        from src.platform.settings.repository import SettingRepository

        models_dir = Settings(SettingRepository()).get_models_dir()
        return deduplicate(scan_native_models(models_dir))

    def prepare_pipes(self, pipes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Inject this backend's device/dtype/VRAM budget into every pipe's config.

        The mirror of ComfyUIBackend.prepare_pipes, which injects host/port: both
        answer "how should this engine instance be driven?". `setdefault` semantics
        mean a preset that sets one of these explicitly on a pipe still wins.
        """
        injected = {
            "device": self.config.device,
            "dtype": self.config.dtype,
            "vram_limit_gb": self.config.gpu_max_vram,
        }

        for pipe in pipes:
            if pipe.get('config') is None:
                pipe['config'] = {}
            for key, value in injected.items():
                pipe['config'].setdefault(key, value)

        # Services that consult the GPU manager directly (MemoryAdvisor,
        # ModelLifecycle) must see the same cap as the pipes do.
        gpu_monitor = getattr(self.generation_engine, 'gpu_monitor', None)
        if gpu_monitor is not None:
            gpu_monitor.set_vram_cap_gb(self.config.gpu_max_vram)

        logger.debug(
            f"[NATIVE_BACKEND] Injected device={injected['device']} "
            f"dtype={injected['dtype']} vram_limit_gb={injected['vram_limit_gb']} "
            f"into {len(pipes)} pipes"
        )
        return pipes

    async def health_check(self) -> Dict[str, Any]:
        """Check the health status of the native backend.

        Status reflects whether the CONFIGURED device is actually usable, not
        whether the host happens to have a GPU at all:
          - device="cpu": always healthy. A CPU-configured backend has no CUDA
            dependency, so `gpu_available` is reported for information only
            and never demotes the status.
          - device="cuda"/"cuda:N": healthy only when torch reports CUDA
            available *and* the requested index actually exists on this host.
            Otherwise every generation routed to this backend will fail at
            load time - reporting "healthy" anyway (the previous behavior)
            hid that until a user hit it. Reports "degraded" up front with a
            plain-language reason and what to do about it instead. "degraded"
            (not a new "unhealthy" tier) reuses the status vocabulary the
            admin UI already renders as a warning badge
            (`BackendsTab.svelte::getHealthVariant`) and the existing
            three-tier healthy/degraded/error status set - a raised exception
            during the check itself is the only case that still reports
            "error".
        """
        try:
            import torch

            configured_device = self.config.device
            is_cuda_configured = configured_device.startswith("cuda")
            cuda_available = torch.cuda.is_available()

            health_info: Dict[str, Any] = {
                "status": "healthy",
                "engine": "native",
                "active_generations": len(self._active),
                "available": self.is_available(),
                "configured_device": configured_device,
                "gpu_available": cuda_available,
            }

            if not is_cuda_configured:
                return health_info

            if not cuda_available:
                health_info["status"] = "degraded"
                health_info["reason"] = (
                    f"Backend is configured to use '{configured_device}' but no CUDA-capable "
                    "GPU is visible to this process. Every generation on this backend will "
                    "fail. Install/fix the NVIDIA driver on this host, or switch this "
                    "backend's device to 'cpu' in Admin -> Backends."
                )
                return health_info

            device_count = torch.cuda.device_count()
            device_index = _cuda_device_index(configured_device)
            if device_index >= device_count:
                health_info["status"] = "degraded"
                health_info["reason"] = (
                    f"Backend is configured to use '{configured_device}', but this host only "
                    f"exposes {device_count} CUDA device(s) (valid indices: "
                    f"0..{device_count - 1}). Fix the device in Admin -> Backends."
                )
                return health_info

            health_info["gpu_name"] = torch.cuda.get_device_name(device_index)
            health_info["gpu_memory_total"] = torch.cuda.get_device_properties(device_index).total_memory
            health_info["gpu_memory_allocated"] = torch.cuda.memory_allocated(device_index)
            return health_info

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "engine": "native"
            }

    async def get_system_info(self) -> Dict[str, Any]:
        """Get system information from the native backend"""
        try:
            import torch
            import psutil
            from pathlib import Path

            from src.platform.runtime.system_memory import get_system_memory

            sys_mem = get_system_memory()
            system_info = {
                "engine": "native",
                "python_version": str(psutil.sys.version_info),
                "cpu_count": psutil.cpu_count(),
                "memory_total": sys_mem.total,
                "memory_available": sys_mem.available,
                "disk_usage": {}
            }

            if torch.cuda.is_available():
                system_info["gpu"] = {
                    "available": True,
                    "device_count": torch.cuda.device_count(),
                    "current_device": torch.cuda.current_device(),
                    "devices": []
                }

                for i in range(torch.cuda.device_count()):
                    device_props = torch.cuda.get_device_properties(i)
                    system_info["gpu"]["devices"].append({
                        "id": i,
                        "name": device_props.name,
                        "total_memory": device_props.total_memory,
                        "allocated_memory": torch.cuda.memory_allocated(i),
                        "cached_memory": torch.cuda.memory_reserved(i)
                    })
            else:
                system_info["gpu"] = {"available": False}

            for directory in ["outputs", "models", "cache"]:
                dir_path = Path(directory)
                if dir_path.exists():
                    try:
                        disk_usage = psutil.disk_usage(str(dir_path))
                        system_info["disk_usage"][directory] = {
                            "total": disk_usage.total,
                            "used": disk_usage.used,
                            "free": disk_usage.free
                        }
                    except Exception:
                        pass

            return system_info

        except Exception as e:
            return {
                "engine": "native",
                "error": str(e)
            }
