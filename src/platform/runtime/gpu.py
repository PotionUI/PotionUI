from pynvml import *
from dataclasses import dataclass, field
from threading import Lock
from typing import Tuple, Optional

from src.platform.observability.logger import logger
from src.platform.runtime import nvml_library
from src.platform.runtime.vram_cap import apply_vram_cap_bytes, get_vram_cap_gb


@dataclass(frozen=True)
class DeviceIdentity:
    """A GPU's own stable hardware identity - deliberately not an index.
    Neither an NVML enumeration index nor a CUDA ordinal is a safe proxy
    for "the same physical card": NVML's enumeration order need not agree
    with CUDA's (itself further remappable by `CUDA_VISIBLE_DEVICES`), so
    "index 0 on both sides" is not proof of anything. `uuid` is NVML's
    canonical `"GPU-xxxxxxxx-...."` form - the same PyTorch and `nvidia-smi`
    both ultimately report (PyTorch's own `.uuid` omits the "GPU-" prefix;
    see `src.features.backends.native_backend._cuda_device_identity`'s
    normalisation) - and is the ONLY field two `DeviceIdentity` values
    compare on (`pci_bus_id` is carried for logs/debugging only, via
    `compare=False`: it's cheap to read from NVML but PyTorch reports PCI
    location in a different shape, so it is never required to agree)."""

    uuid: str
    pci_bus_id: Optional[str] = field(default=None, compare=False)


def effective_vram_budget_gb(
    backend_cap_gb: Optional[float],
    pipe_hint_gb: Optional[float],
    available_gb: Optional[float],
) -> Optional[float]:
    valid_caps = []
    for label, cap in (("backend cap", backend_cap_gb), ("pipe hint", pipe_hint_gb)):
        if cap is None:
            continue
        if cap <= 0:
            logger.warning(
                f"[GPU_MANAGER] Ignoring non-positive VRAM {label} ({cap}); "
                f"a VRAM cap must be > 0 to take effect."
            )
            continue
        valid_caps.append(float(cap))

    if not valid_caps:
        return available_gb
    if available_gb is None:
        return min(valid_caps)
    return min(min(valid_caps), available_gb)


def _nvml_text(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _cuda_device_present(device_index: int) -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available()) and device_index < torch.cuda.device_count()
    except Exception:
        return False


def _torch_device_identity(device_index: int) -> Optional[DeviceIdentity]:
    try:
        import torch

        return DeviceIdentity(uuid=f"GPU-{torch.cuda.get_device_properties(device_index).uuid}")
    except Exception:
        return None


class _CappedMemInfo:
    """Minimal stand-in for NVML's ``nvmlMemory_t`` (``.total``/``.free``/``.used``
    in bytes), used to return a capped reading from ``_get_memory_info``
    without depending on NVML's own (non-constructible) struct type."""

    __slots__ = ("total", "free", "used")

    def __init__(self, total: int, free: int, used: int):
        self.total = total
        self.free = free
        self.used = used


class GpuMonitor:
    """
    GPU Manager - Enhanced GPU and VRAM monitoring

    Provides VRAM management with user-defined budgets and estimation.
    Supports memory profiling and resource allocation decisions.
    """

    def __init__(self, device_index: int = 0):
        self.lock = Lock()
        self.device_index = device_index
        self.device_identity: Optional[DeviceIdentity] = None
        # Set by whichever backend currently owns the GPU (NativeBackend, from its
        # `gpu_max_vram` config) before it runs a pipeline. None = bound only by hardware.
        self._vram_cap_gb: Optional[float] = None

        self.handle = None
        self.source: Optional[str] = None
        nvml_error: Optional[Exception] = None
        try:
            nvml_library.ensure_nvml_library()
            nvmlInit()
            self.handle = nvmlDeviceGetHandleByIndex(device_index)
            self.source = "nvml"
        except Exception as e:
            nvml_error = e
        if self.source is None and _cuda_device_present(device_index):
            self.source = "torch"
        self.available = self.source is not None

        if self.source == "nvml":
            self.device_identity = self._resolve_device_identity()
        elif self.source == "torch":
            self.device_identity = _torch_device_identity(device_index)
            logger.warning(
                f"[GPU_MANAGER] NVML unavailable ({nvml_error}); VRAM readings for "
                f"device {device_index} come from torch.cuda"
            )
        else:
            logger.warning(f"[GPU_MANAGER] No GPU/NVML available - VRAM readings are unknown: {nvml_error}")

        # Debug-only rig-simulation knob (POTIONUI_VRAM_CAP_GB, see vram_cap.py):
        # touching it here logs its loud one-time warning at process startup,
        # right next to the real-hardware line below, instead of buried in the
        # first generation's logs.
        get_vram_cap_gb()

        if self.source == "nvml":
            try:
                gpu_name = nvmlDeviceGetName(self.handle)
                gpu_name_str = gpu_name.decode('utf-8') if isinstance(gpu_name, bytes) else str(gpu_name)
                total_vram_gb = self.get_total_vram() / 1024
                logger.info(f"[GPU_MANAGER] Initialized: {gpu_name_str} with {total_vram_gb:.1f}GB VRAM")
            except Exception as e:
                logger.warning(f"[GPU_MANAGER] Could not get GPU name: {e}")

    def _resolve_device_identity(self) -> Optional[DeviceIdentity]:
        """Best-effort - a live NVML handle does not guarantee every query on
        it succeeds (an older driver, a sandboxed/virtualised GPU). `None`
        here means every consumer wanting physical-identity correspondence
        (e.g. `vram_min_gb`) degrades to `unknown`, never to guessing this
        is any particular card."""
        try:
            uuid = _nvml_text(nvmlDeviceGetUUID(self.handle))
        except Exception:
            return None
        pci_bus_id = None
        try:
            pci_bus_id = _nvml_text(nvmlDeviceGetPciInfo(self.handle).busId)
        except Exception:
            pass
        return DeviceIdentity(uuid=uuid, pci_bus_id=pci_bus_id)

    def _get_memory_info(self):
        if self.source == "torch":
            return self._torch_memory_info()
        if self.source != "nvml":
            return _CappedMemInfo(total=0, free=0, used=0)
        with self.lock:
            info = nvmlDeviceGetMemoryInfo(self.handle)
        if get_vram_cap_gb() is None:
            return info
        capped_free, capped_total = apply_vram_cap_bytes(info.free, info.total)
        return _CappedMemInfo(total=capped_total, free=capped_free, used=info.used)

    def _torch_memory_info(self) -> _CappedMemInfo:
        import torch

        with self.lock:
            free, total = torch.cuda.mem_get_info(self.device_index)
        capped_free, capped_total = apply_vram_cap_bytes(free, total)
        return _CappedMemInfo(total=capped_total, free=capped_free, used=max(0, total - free))

    def get_free_vram(self) -> int:
        """
        Get free VRAM in MB (NVML reported).

        Returns:
            int: Free VRAM in MB
        """
        return self._get_memory_info().free // (1024 * 1024)

    def get_total_vram(self) -> int:
        """
        Get total VRAM in MB.

        Returns:
            int: Total VRAM in MB
        """
        return self._get_memory_info().total // (1024 * 1024)

    def get_available_vram(self) -> Optional[float]:
        import torch

        if not self.available:
            return None
        mem_info = self._get_memory_info()
        free_gb = mem_info.free / (1024**3)

        if torch.cuda.is_available():
            reserved_gb = torch.cuda.memory_reserved() / (1024**3)
            allocated_gb = torch.cuda.memory_allocated() / (1024**3)
            reclaimable_gb = reserved_gb - allocated_gb
            available_gb = free_gb + reclaimable_gb
        else:
            available_gb = free_gb

        return max(0.0, available_gb)

    def set_vram_cap_gb(self, cap_gb: Optional[float]) -> None:
        """
        Set the VRAM cap for the backend that currently owns the GPU.

        Called by NativeBackend before each pipeline run, from its `gpu_max_vram`
        config. This is state, but it is the *owner's* state: only one backend runs
        a pipeline at a time (one GenerationEngine, one cancellation flag).
        """
        self._vram_cap_gb = float(cap_gb) if cap_gb is not None else None

    def get_vram_budget(
        self,
        max_vram_gb: Optional[float] = None,
        safety_margin: float = 0.85,
    ) -> Optional[float]:
        available = self.get_available_vram()
        available_vram = None if available is None else available * safety_margin
        budget = effective_vram_budget_gb(self._vram_cap_gb, max_vram_gb, available_vram)
        logger.debug(
            f"[GPU_MANAGER] VRAM Budget: {budget}GB "
            f"(backend cap: {self._vram_cap_gb}, pipe hint: {max_vram_gb}, "
            f"available: {available_vram}GB)"
        )
        return budget

    def estimate_vram_usage(self, width: int, height: int, model_type: str = "sdxl") -> float:
        """
        Estimate VRAM usage for a given resolution and model type.

        Based on empirical measurements:
        - SDXL 1024x1024: ~6-7GB
        - SDXL 2048x2048: ~10-12GB
        - SDXL 3072x3072: ~18-20GB

        Args:
            width: Image width in pixels
            height: Image height in pixels
            model_type: Model type ("sdxl", "flux", "sd15", etc.)

        Returns:
            float: Estimated VRAM usage in GB
        """
        # Base model loading costs
        base_costs = {
            "sdxl": 4.0,
            "flux": 8.0,
            "sd15": 2.0,
            "qwen_image": 6.0,
        }
        base_vram = base_costs.get(model_type.lower(), 4.0)

        # Calculate additional VRAM based on resolution
        pixels = width * height
        megapixels = pixels / (1024 * 1024)

        # Non-linear scaling for SDXL (more efficient at larger sizes)
        if model_type.lower() == "sdxl":
            if megapixels <= 1:  # Up to 1024x1024
                additional_vram = megapixels * 3.0
            elif megapixels <= 4:  # Up to 2048x2048
                additional_vram = 3.0 + (megapixels - 1) * 2.0
            elif megapixels <= 9:  # Up to 3072x3072
                additional_vram = 9.0 + (megapixels - 4) * 1.8
            else:  # Beyond 3072x3072
                additional_vram = 18.0 + (megapixels - 9) * 1.5
        else:
            # Linear scaling for other models
            additional_vram = megapixels * 2.0

        total_vram = base_vram + additional_vram

        # Add 10% overhead for safety
        return total_vram * 1.1

    def __del__(self):
        if getattr(self, "source", None) != "nvml":
            return
        try:
            nvmlShutdown()
        except Exception as e:
            logger.debug(f"NVML cleanup error (can be ignored): {e}")
