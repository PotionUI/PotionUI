from __future__ import annotations

import ctypes
import os
import sys
import threading
from pathlib import Path
from typing import Mapping, Optional

import pynvml

from src.platform.observability.logger import logger

_lock = threading.Lock()


def _running_on_windows() -> bool:
    return sys.platform == "win32"


def _load_dll(path: Path):
    return ctypes.CDLL(str(path))


def windows_nvml_candidates(environ: Mapping[str, str]) -> tuple[Path, ...]:
    windows_dir = environ.get("WINDIR") or environ.get("SystemRoot") or "C:\\Windows"
    program_files = environ.get("ProgramFiles") or "C:\\Program Files"
    return (
        Path(windows_dir) / "System32" / "nvml.dll",
        Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvml.dll",
    )


def ensure_nvml_library() -> Optional[Path]:
    if not _running_on_windows():
        return None
    with _lock:
        if pynvml.nvmlLib is not None:
            return None
        for candidate in windows_nvml_candidates(os.environ):
            try:
                library = _load_dll(candidate)
            except OSError:
                continue
            pynvml.nvmlLib = library
            logger.info(f"[NVML] Loaded {candidate}")
            return candidate
    return None
