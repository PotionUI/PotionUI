from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Mapping

from src.platform.runtime.cuda_env import ALLOCATOR_CONF_VARS, is_wsl

DRIVER_OOM_POLICY_ENV = "POTIONUI_CUDA_DRIVER_OOM"
RESTART_HINT = (
    "This process can no longer use the GPU: restart it with Restart Backend "
    "(Administration > Backends) before the next generation."
)
RESTART_ACTION = "Restart PotionUI: Administration > Backends > Restart Backend"
DRIVER_OOM_MESSAGES = (
    "CUDA driver error: device not ready",
    "CUDA driver error: out of memory",
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_damage: str | None = None


class CudaAllocatorDamaged(RuntimeError):
    def __init__(self, cause: str) -> None:
        super().__init__(
            f"A CUDA out-of-memory under expandable segments left this process's GPU allocator "
            f"unusable ({cause}). {RESTART_HINT}"
        )


def allocator_conf(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return next((env[var] for var in ALLOCATOR_CONF_VARS if var in env), "")


def expandable_segments_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = re.findall(r"expandable_segments:(\w+)", re.sub(r"\s", "", allocator_conf(environ)))
    return bool(values) and values[-1] == "True"


def driver_oom_is_fatal(environ: Mapping[str, str] | None = None, kernel_release: str | None = None) -> bool:
    env = os.environ if environ is None else environ
    policy = env.get(DRIVER_OOM_POLICY_ENV, "").strip().lower()
    if policy in ("fatal", "retry"):
        return policy == "fatal"
    return is_wsl(kernel_release)


def is_driver_oom(exc: BaseException) -> bool:
    return isinstance(exc, RuntimeError) and str(exc).startswith(DRIVER_OOM_MESSAGES)


def driver_oom_retries() -> bool:
    return not driver_oom_is_fatal() or not expandable_segments_enabled()


def record_driver_oom(exc: BaseException) -> None:
    global _damage
    if not is_driver_oom(exc) or driver_oom_retries():
        return
    with _lock:
        first = _damage is None
        if first:
            _damage = str(exc)
    if first:
        logger.error(
            "CUDA driver error under expandable segments (%s): no retry in this process. %s", exc, RESTART_HINT
        )
        _stop_growing_expandable_segments()


def allocator_damage() -> str | None:
    return _damage


def needs_gpu_restart(exc: BaseException) -> bool:
    if isinstance(exc, CudaAllocatorDamaged):
        return True
    if is_driver_oom(exc) and not driver_oom_retries():
        return True
    return _damage is not None and "CUDA driver error:" in str(exc)


def raise_if_allocator_damaged() -> None:
    damage = _damage
    if damage is not None:
        raise CudaAllocatorDamaged(damage)


def _stop_growing_expandable_segments() -> None:
    conf = re.sub(r"\s", "", allocator_conf())
    settings = re.sub(r"expandable_segments:\w+", "expandable_segments:False", conf)
    try:
        import torch

        torch.cuda.memory._set_allocator_settings(settings)
    except Exception:
        logger.warning("Could not turn expandable segments off after a CUDA driver OOM", exc_info=True)
