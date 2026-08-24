"""Per-component device assignment for the native engine.

Decides which CUDA device (or CPU) each of the three heavy components — the
diffusion transformer (DiT), the text encoder (TE) and the VAE — lives on.

This is NOT tensor parallelism: every component runs whole on one device. The
only cross-device move we make is spilling the TE onto a second GPU when the
DiT would otherwise crowd its own device, so both can stay resident.

All ``torch.cuda`` queries are injectable so the logic is unit-testable without
a GPU.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import torch

from src.platform.runtime.vram_cap import apply_vram_cap_bytes

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024 ** 3
# Spill the TE to another GPU only when the DiT alone would dominate its device.
_DIT_DOMINATES_FRACTION = 0.70


@dataclass(frozen=True)
class DevicePlan:
    """Device string per component, e.g. ``"cuda:0"`` / ``"cpu"``."""

    dit_device: str
    te_device: str
    vae_device: str


def _device_index(device: str) -> int:
    if ":" in device:
        return int(device.split(":", 1)[1])
    return 0


def _capped_mem_get_info(idx: int) -> tuple[int, int]:
    """Real ``torch.cuda.mem_get_info``, subject to the ``POTIONUI_VRAM_CAP_GB``
    rig-simulation cap (see ``src.platform.runtime.vram_cap``) — a no-op when
    that env var is unset. Only used as the default when the caller doesn't
    inject its own ``mem_get_info`` (tests always inject a fake)."""
    free, total = torch.cuda.mem_get_info(idx)
    return apply_vram_cap_bytes(free, total)


def make_device_plan(
    preferred: str | None = None,
    dit_gb: float | None = None,
    *,
    cuda_available: Callable[[], bool] | None = None,
    device_count: Callable[[], int] | None = None,
    mem_get_info: Callable[[int], tuple[int, int]] | None = None,
) -> DevicePlan:
    """Assign components to devices.

    Single-GPU / default: every component on ``preferred`` (or ``cuda:0``).

    Multi-GPU: if ``dit_gb`` is given and the DiT alone would consume more than
    70% of its device's total memory, the TE is moved to the *other* device
    with the most free memory. The VAE always stays with the DiT (it decodes
    the DiT's latents).

    Falls back to all-CPU when CUDA is unavailable. The ``cuda_available`` /
    ``device_count`` / ``mem_get_info`` hooks default to the real ``torch.cuda``
    functions and exist so tests can inject fake topologies.
    """
    cuda_available = cuda_available or torch.cuda.is_available
    device_count = device_count or torch.cuda.device_count
    mem_get_info = mem_get_info or _capped_mem_get_info

    if not cuda_available():
        logger.debug("no CUDA -> all components on CPU")
        return DevicePlan("cpu", "cpu", "cpu")

    dit_device = preferred or "cuda:0"
    te_device = dit_device
    vae_device = dit_device

    count = device_count()
    if count > 1 and dit_gb is not None:
        dit_idx = _device_index(dit_device)
        _, total = mem_get_info(dit_idx)
        total_gb = total / _BYTES_PER_GB
        if total_gb > 0 and dit_gb > _DIT_DOMINATES_FRACTION * total_gb:
            others = [i for i in range(count) if i != dit_idx]
            best = max(others, key=lambda i: mem_get_info(i)[0])
            te_device = f"cuda:{best}"
            logger.debug(
                "DiT %.1fGB dominates %s (%.1fGB); spilling TE to cuda:%d",
                dit_gb, dit_device, total_gb, best,
            )

    return DevicePlan(dit_device=dit_device, te_device=te_device, vae_device=vae_device)
