from pynvml import *
from dataclasses import dataclass, field
from threading import Lock
from typing import Tuple, Optional

from src.platform.observability.logger import logger
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
    available_gb: float,
) -> float:
    """Compose the model-loading/placement VRAM budget from the backend's
    configured cap (``NativeBackendConfig.gpu_max_vram``, applied via
    ``GpuMonitor.set_vram_cap_gb``) and an optional per-pipe hint (a preset's
    `vram_limit_gb` on one pipe), then bound the result by hardware.

    Semantics:
        - ``None`` means "no cap from that source" - it never affects the result.
        - A cap `<= 0` is invalid configuration, not "unlimited" or "raise the
          budget": it is ignored with a warning, exactly like ``None``.
        - The two caps compose as the stricter (lesser) of the two valid ones,
          so a pipe hint can only ever lower the budget below the backend's
          configured maximum, never raise it - the result does not depend on
          which of the two is passed first.
        - With both absent, the result is bounded by ``available_gb`` alone
          (today's uncapped behaviour).
        - This is a planning budget for model-loading/placement decisions, not
          an OS-enforced VRAM quota and not a guarantee a generation fits.
    """
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

    return min(min(valid_caps), available_gb)


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
        """
        Initialize GPU manager.

        This is a host-level service: it reports on the GPU this process can see -
        specifically `device_index` (default 0), the only one anything in this
        process actually reads from today (there is exactly one process-wide
        `GpuMonitor` singleton - see `src.bootstrap.container`). It deliberately
        holds no VRAM *budget* - that is a property of the native backend
        (NativeBackendConfig.gpu_max_vram), passed in per call, because a budget
        is engine configuration while the hardware is not.

        `device_index` is recorded as `self.device_index` (display/log only -
        an NVML enumeration index is not itself proof of correspondence with
        anything, see `DeviceIdentity`) and, once NVML confirms a handle,
        `self.device_identity` is resolved once (`DeviceIdentity` - a stable
        UUID, not an index) so a caller with per-device evidence (e.g. a
        preset requirement checker holding a `NativeBackend`'s own resolved
        `ExecutionDeviceEvidence.identity`) can confirm THIS monitor's
        reading actually corresponds to that exact physical card before
        trusting it - never by comparing indices alone.

        No NVIDIA driver/GPU is a legitimate host state (CPU-only hosts are
        supported for claim/setup work) - construction must never raise. `nvmlInit`
        is attempted once here; failure leaves `self.available = False` and every
        getter below reports zeroed-out readings instead of touching NVML again,
        mirroring `SystemMonitor.__init__` (src/platform/observability/system_probe.py).
        """
        self.lock = Lock()
        self.device_index = device_index
        self.device_identity: Optional[DeviceIdentity] = None
        # Set by whichever backend currently owns the GPU (NativeBackend, from its
        # `gpu_max_vram` config) before it runs a pipeline. None = bound only by hardware.
        self._vram_cap_gb: Optional[float] = None

        self.handle = None
        self.available = False
        try:
            nvmlInit()
            self.handle = nvmlDeviceGetHandleByIndex(device_index)
            self.available = True
        except Exception as e:
            logger.warning(f"[GPU_MANAGER] No GPU/NVML available - VRAM readings will report 0: {e}")

        if self.available:
            self.device_identity = self._resolve_device_identity()

        # Debug-only rig-simulation knob (POTIONUI_VRAM_CAP_GB, see vram_cap.py):
        # touching it here logs its loud one-time warning at process startup,
        # right next to the real-hardware line below, instead of buried in the
        # first generation's logs.
        get_vram_cap_gb()

        # Log GPU information
        if self.available:
            try:
                gpu_name = nvmlDeviceGetName(self.handle)
                gpu_name_str = gpu_name.decode('utf-8') if isinstance(gpu_name, bytes) else str(gpu_name)
                total_vram_gb = self.get_total_vram() / 1024  # Convert MB to GB
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
            uuid = nvmlDeviceGetUUID(self.handle)
        except Exception:
            return None
        pci_bus_id = None
        try:
            pci_bus_id = nvmlDeviceGetPciInfo(self.handle).busId
        except Exception:
            pass
        return DeviceIdentity(uuid=uuid, pci_bus_id=pci_bus_id)

    def _get_memory_info(self):
        """Get NVML memory info (thread-safe).

        When ``POTIONUI_VRAM_CAP_GB`` is set (rig-simulation harness, see
        ``vram_cap.py``), ``total``/``free`` are capped to it before any
        caller sees them - ``used`` is left as the real, driver-reported
        figure, since real tensors still occupy real VRAM. Every getter on
        this class (``get_total_vram``, ``get_free_vram``,
        ``get_available_vram``, ...) reads through this one method, so
        capping here is sufficient to cap every admission decision that goes
        through ``GpuMonitor``.

        Without a GPU (``self.available`` is False) this returns all zeros
        rather than touching NVML.
        """
        if not self.available:
            return _CappedMemInfo(total=0, free=0, used=0)
        with self.lock:
            info = nvmlDeviceGetMemoryInfo(self.handle)
        if get_vram_cap_gb() is None:
            return info
        capped_free, capped_total = apply_vram_cap_bytes(info.free, info.total)
        return _CappedMemInfo(total=capped_total, free=capped_free, used=info.used)

    def get_free_vram(self) -> int:
        """
        Get free VRAM in MB (NVML reported).

        Returns:
            int: Free VRAM in MB
        """
        return self._get_memory_info().free // (1024 * 1024)

    def get_used_vram(self) -> int:
        """
        Get used VRAM in MB (NVML reported).

        Returns:
            int: Used VRAM in MB
        """
        return self._get_memory_info().used // (1024 * 1024)

    def get_total_vram(self) -> int:
        """
        Get total VRAM in MB.

        Returns:
            int: Total VRAM in MB
        """
        return self._get_memory_info().total // (1024 * 1024)

    def get_available_vram(self) -> float:
        """
        Get actually available VRAM in GB.

        This accounts for PyTorch reserved but unallocated memory,
        providing a more accurate picture of what's usable.

        Returns:
            float: Available VRAM in GB
        """
        import torch

        mem_info = self._get_memory_info()
        free_gb = mem_info.free / (1024**3)

        # Add PyTorch reserved but not allocated memory
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
    ) -> float:
        """
        Get VRAM budget in GB: the stricter of the owning backend's configured cap
        and an explicit per-pipe hint, bounded by what is actually available.

        Args:
            max_vram_gb: An explicit cap, e.g. a pipe's `vram_limit_gb` config key.
                Composes with the owning backend's cap via `effective_vram_budget_gb`
                - it can lower the budget below the backend's configured maximum,
                never raise it. When neither is present, only the hardware bounds
                the budget.
            safety_margin: Multiply available VRAM by this factor for safety (default 0.85)

        Returns:
            float: VRAM budget in GB
        """
        available_vram = self.get_available_vram() * safety_margin
        budget = effective_vram_budget_gb(self._vram_cap_gb, max_vram_gb, available_vram)
        logger.debug(
            f"[GPU_MANAGER] VRAM Budget: {budget:.2f}GB "
            f"(backend cap: {self._vram_cap_gb}, pipe hint: {max_vram_gb}, "
            f"available: {available_vram:.2f}GB)"
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

    def can_fit_in_vram(self, estimated_usage: float, safety_margin: float = 0.85) -> bool:
        """
        Check if estimated operation fits in VRAM budget.

        Args:
            estimated_usage: Estimated VRAM usage in GB
            safety_margin: Safety margin multiplier (default 0.85 = 85%)

        Returns:
            bool: True if operation fits, False otherwise
        """
        budget = self.get_vram_budget(safety_margin=safety_margin)
        fits = estimated_usage <= budget

        logger.debug(
            f"[GPU_MANAGER] VRAM Check: {estimated_usage:.2f}GB needed vs "
            f"{budget:.2f}GB budget = {'OK' if fits else 'INSUFFICIENT'}"
        )

        return fits

    def log_vram_status(self, context: str = ""):
        """
        Log current VRAM status for debugging.

        Args:
            context: Optional context string for log message
        """
        import torch

        total_mb = self.get_total_vram()
        used_mb = self.get_used_vram()
        free_mb = self.get_free_vram()
        available_gb = self.get_available_vram()
        budget_gb = self.get_vram_budget()

        context_str = f"[{context}] " if context else ""

        # PyTorch memory stats
        if torch.cuda.is_available():
            allocated_gb = torch.cuda.memory_allocated() / (1024**3)
            reserved_gb = torch.cuda.memory_reserved() / (1024**3)
            logger.debug(
                f"[GPU_MANAGER] {context_str}VRAM Status:\n"
                f"  Total: {total_mb / 1024:.2f}GB\n"
                f"  Used: {used_mb / 1024:.2f}GB\n"
                f"  Free: {free_mb / 1024:.2f}GB\n"
                f"  Available: {available_gb:.2f}GB\n"
                f"  Budget: {budget_gb:.2f}GB\n"
                f"  PyTorch Allocated: {allocated_gb:.2f}GB\n"
                f"  PyTorch Reserved: {reserved_gb:.2f}GB"
            )
        else:
            logger.debug(
                f"[GPU_MANAGER] {context_str}VRAM Status:\n"
                f"  Total: {total_mb / 1024:.2f}GB\n"
                f"  Used: {used_mb / 1024:.2f}GB\n"
                f"  Free: {free_mb / 1024:.2f}GB\n"
                f"  Available: {available_gb:.2f}GB\n"
                f"  Budget: {budget_gb:.2f}GB"
            )

    def get_temperature(self) -> int:
        """
        Get GPU temperature in Celsius.

        Returns:
            int: GPU temperature in °C, or 0 if no GPU is available.
        """
        if not self.available:
            return 0
        with self.lock:
            return nvmlDeviceGetTemperature(self.handle, NVML_TEMPERATURE_GPU)

    def __del__(self):
        """Cleanup NVML on destruction."""
        if not getattr(self, "available", False):
            return
        try:
            nvmlShutdown()
        except Exception as e:
            logger.debug(f"NVML cleanup error (can be ignored): {e}")
