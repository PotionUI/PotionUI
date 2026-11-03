from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping
from pathlib import Path

CUDA_CACHE_MAXSIZE_BYTES = 4 * 1024**3
CUDA_CACHE_SUBDIR = "cuda_cache"
ALLOCATOR_CONF_VARS = ("PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_HIP_ALLOC_CONF", "PYTORCH_ALLOC_CONF")


def _running_kernel_release() -> str:
    uname = getattr(os, "uname", None)
    return uname().release if uname is not None else ""


def is_wsl(kernel_release: str | None = None) -> bool:
    release = _running_kernel_release() if kernel_release is None else kernel_release
    return "microsoft" in release.lower()


def apply_cuda_env_defaults(
    environ: MutableMapping[str, str] | None = None,
    platform: str | None = None,
) -> dict[str, str]:
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    applied: dict[str, str] = {}

    def set_default(key: str, value: str) -> None:
        if key not in env:
            env[key] = value
            applied[key] = value

    set_default("CUDA_CACHE_MAXSIZE", str(CUDA_CACHE_MAXSIZE_BYTES))

    if "CUDA_CACHE_PATH" not in env:
        cache_dir = Path(env.get("POTIONUI_STORAGE_PATH") or "storage").resolve() / CUDA_CACHE_SUBDIR
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        else:
            set_default("CUDA_CACHE_PATH", str(cache_dir))

    if not platform.startswith("win") and not any(var in env for var in ALLOCATOR_CONF_VARS):
        set_default("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    return applied
