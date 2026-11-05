from __future__ import annotations

import importlib.metadata
import importlib.util
import threading
from dataclasses import dataclass

import torch

from src.platform.runtime.gpu_generation import GpuGeneration, generation_for

SDPA = "sdpa"
FLASH = "flash"
SAGE = "sage"
SAGE2 = "sage2"
SAGE3 = "sage3"
SPARGE = "sparge"

_SAGE3_CAPABILITIES: frozenset[tuple[int, int]] = frozenset({(10, 0), (12, 0), (12, 1)})
_SAGE3_MIN_CUDA_RUNTIME: tuple[int, int] = (12, 8)
_SPARGE_CAPABILITY_MAJORS: frozenset[int] = frozenset({8, 9})
_PROBED_MODULES: tuple[str, ...] = ("triton", "sageattention", "flash_attn", "sageattn3", "spas_sage_attn")
_CPU_NAME = "cpu"
VENDOR_CUDA = "cuda"
VENDOR_HIP = "hip"
VENDOR_NONE = "none"


@dataclass(frozen=True)
class DeviceCapabilities:
    index: int | None
    name: str | None
    capability: tuple[int, int]
    attention_backends: frozenset[str]
    total_memory: int = 0
    vendor: str = VENDOR_CUDA

    @property
    def generation(self) -> GpuGeneration:
        return generation_for(self.capability)

    def at_least(self, major: int, minor: int = 0) -> bool:
        return self.capability >= (major, minor)


@dataclass(frozen=True)
class Capabilities:
    devices: tuple[DeviceCapabilities, ...]
    cpu: DeviceCapabilities
    triton: bool = False

    def device(self, index: int | torch.device | str | None = None) -> DeviceCapabilities:
        resolved = _resolve_index(index) if self.devices else None
        if resolved is None or not 0 <= resolved < len(self.devices):
            return self.cpu
        return self.devices[resolved]


def _resolve_index(index: int | torch.device | str | None) -> int | None:
    if isinstance(index, str):
        index = torch.device(index)
    if isinstance(index, torch.device):
        if index.type != "cuda":
            return None
        index = index.index
    if index is None:
        return _current_device()
    return index


def _current_device() -> int | None:
    try:
        return int(torch.cuda.current_device())
    except Exception:
        return None


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _sageattention_is_v2() -> bool:
    try:
        return importlib.metadata.version("sageattention").startswith("2")
    except Exception:
        return False


def _cuda_runtime_version() -> tuple[int, int]:
    raw = getattr(torch.version, "cuda", None)
    if not raw:
        return (0, 0)
    parts = raw.split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return (0, 0)


def _device_count() -> int:
    try:
        return int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    except Exception:
        return 0


def _device_capability(index: int) -> tuple[int, int]:
    try:
        major, minor = torch.cuda.get_device_capability(index)
        return (int(major), int(minor))
    except Exception:
        return (0, 0)


def _device_name(index: int) -> str | None:
    try:
        return str(torch.cuda.get_device_name(index))
    except Exception:
        return None


def _device_total_memory(index: int) -> int:
    try:
        return int(torch.cuda.get_device_properties(index).total_memory)
    except Exception:
        return 0

def _vendor() -> str:
    try:
        return VENDOR_HIP if getattr(torch.version, "hip", None) is not None else VENDOR_CUDA
    except Exception:
        return VENDOR_CUDA

def _attention_backends(
    capability: tuple[int, int],
    modules: frozenset[str],
    sage_v2: bool,
    cuda_runtime: tuple[int, int],
) -> frozenset[str]:
    major = capability[0]
    found = {SDPA}
    if "flash_attn" in modules:
        found.add(FLASH)
    sage = "sageattention" in modules and "triton" in modules and major >= 7
    if sage:
        found.add(SAGE)
    if sage and major >= 8 and sage_v2:
        found.add(SAGE2)
    if (
        "sageattn3" in modules
        and capability in _SAGE3_CAPABILITIES
        and cuda_runtime >= _SAGE3_MIN_CUDA_RUNTIME
    ):
        found.add(SAGE3)
    if "spas_sage_attn" in modules and major in _SPARGE_CAPABILITY_MAJORS:
        found.add(SPARGE)
    return frozenset(found)


def _build() -> Capabilities:
    modules = frozenset(name for name in _PROBED_MODULES if _has_module(name))
    sage_v2 = "sageattention" in modules and _sageattention_is_v2()
    cuda_runtime = _cuda_runtime_version()
    vendor = _vendor()
    devices = []
    for index in range(_device_count()):
        capability = _device_capability(index)
        devices.append(
            DeviceCapabilities(
                index=index,
                name=_device_name(index),
                capability=capability,
                attention_backends=_attention_backends(capability, modules, sage_v2, cuda_runtime),
                total_memory=_device_total_memory(index),
                vendor=vendor,
            )
        )
    cpu = DeviceCapabilities(
        index=None,
        name=_CPU_NAME,
        capability=(0, 0),
        attention_backends=frozenset({SDPA}),
        vendor=VENDOR_NONE,
    )
    return Capabilities(devices=tuple(devices), cpu=cpu, triton="triton" in modules)


_lock = threading.Lock()
_snapshot: Capabilities | None = None


def capabilities() -> Capabilities:
    global _snapshot
    snapshot = _snapshot
    if snapshot is None:
        with _lock:
            snapshot = _snapshot
            if snapshot is None:
                snapshot = _snapshot = _build()
    return snapshot


def refresh_capabilities() -> Capabilities:
    global _snapshot
    with _lock:
        importlib.invalidate_caches()
        _snapshot = _build()
        return _snapshot


def _install_snapshot(snapshot: Capabilities | None) -> Capabilities | None:
    global _snapshot
    with _lock:
        previous = _snapshot
        _snapshot = snapshot
    return previous
