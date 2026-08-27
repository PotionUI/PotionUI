"""Hardware-derived defaults for the auto-provisioned native backend.

`NativeBackendConfig` used to hardcode device="cuda", dtype="float16",
gpu_max_vram=8 regardless of what the host actually has (or lacks). A GPU-less
box silently got a cuda/float16/8GB config that fails at generation time, and a
90GB-class card was capped at 8GB. This module inspects the host once and
derives values that actually match it.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from src.platform.observability.logger import logger


@dataclass(frozen=True)
class NativeHardwareDefaults:
    """What a freshly auto-provisioned native backend should default to."""
    device: str
    dtype: str
    gpu_max_vram: int


@lru_cache(maxsize=1)
def detect_native_hardware_defaults() -> NativeHardwareDefaults:
    """Probe this host once per process and derive device/dtype/gpu_max_vram.

    Reuses the same primitives the native engine consults at inference time,
    so an auto-provisioned backend's defaults agree with what the engine will
    actually do with them:
      - `total_vram_gb()` (`native/memory/residency.py`) for the VRAM total -
        the same deterministic call the fp8-quantise-at-load gate uses (total,
        not load-moment-free, so the derived cap doesn't wobble with whatever
        else happens to be resident at auto-provision time).
      - `minimum_inference_memory_gb()` (same module) for the reserve carved
        off the ceiling - the exact headroom the native engine already keeps
        free on top of resident weights, so this doesn't invent a second,
        inconsistent margin.
      - bf16 on Ampere-or-newer (SM80+), else fp16, mirroring
        `native/ops/dtype.py::_supports_bf16` and matching what every native
        pipe's own `PipeConfigSpec` already defaults its `dtype` to (LTX,
        Krea-2, Maya, Wan2.2 chain, ...); float16 was only ever correct
        for pre-Ampere cards.

    Only probes (`torch.cuda.is_available()`, `get_device_capability`,
    `mem_get_info`) - never allocates, so it's safe to call on a box whose GPU
    a maintainer is actively generating on.

    Cached for the process lifetime: `NativeBackendConfig` is reconstructed on
    every `BackendConfigStore.get_backends()` call while the auto-provisioned
    backend's persisted config stays `{}` (nothing ever writes to it unless an
    admin edits the backend), so without caching this would re-probe on every
    request. Hardware doesn't change mid-process; call `.cache_clear()` to
    force a re-probe (tests only).
    """
    try:
        import torch
    except ImportError:
        logger.warning("[NATIVE_HARDWARE] torch not importable; defaulting device=cpu dtype=float32")
        return NativeHardwareDefaults(device="cpu", dtype="float32", gpu_max_vram=0)

    if not torch.cuda.is_available():
        logger.info("[NATIVE_HARDWARE] No CUDA device detected; defaulting device=cpu dtype=float32")
        return NativeHardwareDefaults(device="cpu", dtype="float32", gpu_max_vram=0)

    from src.platform.runtime.native.memory.residency import (
        minimum_inference_memory_gb,
        total_vram_gb,
    )

    try:
        gpu_name = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001 - a driver hiccup must not break provisioning
        gpu_name = "CUDA GPU"

    try:
        major, minor = torch.cuda.get_device_capability(0)
    except Exception:  # noqa: BLE001
        major, minor = 0, 0

    dtype = "bfloat16" if major >= 8 else "float16"

    total_gb = total_vram_gb("cuda") or 0.0
    reserve_gb = minimum_inference_memory_gb()
    gpu_max_vram = max(1, int(total_gb - reserve_gb))

    logger.info(
        "[NATIVE_HARDWARE] Detected %s, %.1fGB VRAM, sm%d%d; defaulting "
        "device=cuda dtype=%s gpu_max_vram=%dGB (%.1fGB total - %.1fGB reserve)",
        gpu_name, total_gb, major, minor, dtype, gpu_max_vram, total_gb, reserve_gb,
    )
    return NativeHardwareDefaults(device="cuda", dtype=dtype, gpu_max_vram=gpu_max_vram)
