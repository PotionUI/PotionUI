"""System discovery for the native-engine "Optimizations" panel.

Answers one question: *can this machine build/run the acceleration libraries
the native engine knows about?* ``probe_system()`` gathers GPU capability, the
CUDA build toolchain (nvcc/gcc/Python headers), and the versions of the
attention-kernel packages already installed, so ``catalog.py`` can decide which
optimizations are installable without touching torch/subprocess/importlib
itself (that seam is what the tests monkeypatch).
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Optional

import torch

from src.platform.runtime.native import attention

_NVCC_RELEASE_RE = re.compile(r"release (\d+)\.(\d+)")


@dataclass
class SystemProbe:
    """A snapshot of this host's ability to build/run native attention kernels."""

    # GPU
    cuda_available: bool = False
    compute_capability: Optional[tuple[int, int]] = None
    gpu_name: Optional[str] = None
    gpu_vram_gb: Optional[float] = None

    # Interpreter
    python_version: tuple[int, int] = (0, 0)

    # Torch
    torch_version: str = ""
    torch_cuda_version: Optional[str] = None

    # Build toolchain
    nvcc_found: bool = False
    nvcc_version: Optional[tuple[int, int]] = None
    nvcc_cuda_matches_torch: bool = False
    nvcc_source: Optional[str] = None  # "system" | "venv" | None
    gcc_found: bool = False
    python_h_found: bool = False

    # Installed acceleration libs (None = not installed)
    sageattention_version: Optional[str] = None
    sageattn3_version: Optional[str] = None
    sparge_version: Optional[str] = None
    triton_version: Optional[str] = None
    flash_attn_version: Optional[str] = None
    xformers_version: Optional[str] = None

    # Attention dispatcher state
    active_backend: str = "sdpa"
    available_backends: list[str] = field(default_factory=lambda: ["sdpa"])


def _package_version(name: str) -> Optional[str]:
    try:
        return _pkg_version(name)
    except PackageNotFoundError:
        return None


def _gpu_info() -> tuple[bool, Optional[tuple[int, int]], Optional[str], Optional[float]]:
    cuda_available = torch.cuda.is_available()
    if not cuda_available:
        return False, None, None, None

    try:
        capability = torch.cuda.get_device_capability()
    except Exception:  # noqa: BLE001 — a driver hiccup must not break probing
        capability = None

    gpu_name: Optional[str] = None
    gpu_vram_gb: Optional[float] = None
    try:
        from pynvml import nvmlDeviceGetName

        from src.platform.runtime.gpu import GpuManager

        gpu_manager = GpuManager()
        raw_name = nvmlDeviceGetName(gpu_manager.handle)
        gpu_name = raw_name.decode("utf-8") if isinstance(raw_name, bytes) else str(raw_name)
        gpu_vram_gb = gpu_manager.get_total_vram() / 1024
    except Exception:  # noqa: BLE001 — GpuManager needs NVML; absence isn't fatal
        pass

    if gpu_name is None:
        try:
            gpu_name = torch.cuda.get_device_name(0)
        except Exception:  # noqa: BLE001
            gpu_name = None

    return cuda_available, capability, gpu_name, gpu_vram_gb


def cuda_major_of(cuda_version: Optional[str]) -> Optional[int]:
    """Parse the CUDA major version out of a "X.Y"-style string (torch's
    ``torch.version.cuda``, or an nvcc ``--version`` release). Shared by
    catalog.py so "which CUDA series is this" is computed exactly one way.
    """
    if not cuda_version:
        return None
    head = cuda_version.split(".")[0]
    return int(head) if head.isdigit() else None


def _find_venv_nvcc(major: Optional[int]) -> Optional[Path]:
    """Locate `nvcc` inside a pip-installed CUDA-`major`-series package.

    Verified 2026-07-11 by inspecting the actual wheels: CUDA <=12 series'
    `-cu{major}`-suffixed packages (e.g. `nvidia-cuda-nvcc-cu12`) ship only
    `ptxas` + libNVVM (Numba's JIT backend), never a real `nvcc` driver.
    Starting with the CUDA 13.x unsuffixed repackaging, NVIDIA ships the full
    compiler (`nvcc`, `cudafe++`, `fatbinary`, `nvlink`, ...) under a single
    `nvidia/cu{major}/` prefix - so for major >= 13 this is a real, working
    toolchain. This function doesn't special-case "13" itself: it just checks
    whatever `nvidia.cu{major}` resolves to for the major torch reports, so a
    correctly-packaged CUDA 14/15/... series is picked up automatically.
    """
    if major is None:
        return None
    try:
        spec = importlib.util.find_spec(f"nvidia.cu{major}")
    except (ImportError, ModuleNotFoundError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None

    for location in spec.submodule_search_locations:
        candidate = Path(location) / "bin" / "nvcc"
        if candidate.exists():
            return candidate
    return None


def _run_nvcc_version(nvcc_path: str) -> Optional[tuple[int, int]]:
    try:
        result = subprocess.run(
            [nvcc_path, "--version"], capture_output=True, text=True, timeout=5,
        )
        output = result.stdout or result.stderr or ""
    except Exception:  # noqa: BLE001 — a broken nvcc must not break probing
        return None

    match = _NVCC_RELEASE_RE.search(output)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _nvcc_major_matches_torch(nvcc_version: Optional[tuple[int, int]], torch_cuda_version: Optional[str]) -> bool:
    """Major-version match only: nvcc 13.1 matches a torch built against cuda 13.0."""
    if nvcc_version is None:
        return False
    torch_major = cuda_major_of(torch_cuda_version)
    return torch_major is not None and torch_major == nvcc_version[0]


def _nvcc_info(torch_cuda_version: Optional[str]) -> tuple[bool, Optional[tuple[int, int]], bool, Optional[str]]:
    """Discover nvcc from the system PATH and from a pip-installed venv package
    for torch's own CUDA major, preferring whichever one's CUDA major matches
    torch's. Returns (found, version, matches_torch, source), source being
    "system"/"venv"/None.
    """
    torch_major = cuda_major_of(torch_cuda_version)
    candidates: list[tuple[str, str]] = []

    system_nvcc = shutil.which("nvcc")
    if system_nvcc:
        candidates.append((system_nvcc, "system"))

    venv_nvcc = _find_venv_nvcc(torch_major)
    if venv_nvcc:
        candidates.append((str(venv_nvcc), "venv"))

    if not candidates:
        return False, None, False, None

    probed = [
        (source, _run_nvcc_version(path))
        for path, source in candidates
    ]

    for source, version in probed:
        if _nvcc_major_matches_torch(version, torch_cuda_version):
            return True, version, True, source

    # Nothing matches torch's CUDA major - report the first one found (system
    # preferred over venv, matching `candidates`' construction order) so the
    # UI can still show *something* installed, just flagged as mismatched.
    source, version = probed[0]
    return True, version, False, source


def _python_h_found() -> bool:
    include_dir = sysconfig.get_paths().get("include")
    if not include_dir:
        return False
    return (Path(include_dir) / "Python.h").exists()


def probe_system() -> SystemProbe:
    """Probe this host for GPU/toolchain/library facts. Cheap enough to call per request."""
    cuda_available, capability, gpu_name, gpu_vram_gb = _gpu_info()
    torch_cuda_version = getattr(torch.version, "cuda", None)
    nvcc_found, nvcc_version, nvcc_matches, nvcc_source = _nvcc_info(torch_cuda_version)

    return SystemProbe(
        cuda_available=cuda_available,
        compute_capability=capability,
        gpu_name=gpu_name,
        gpu_vram_gb=gpu_vram_gb,
        python_version=(sys.version_info.major, sys.version_info.minor),
        torch_version=torch.__version__,
        torch_cuda_version=torch_cuda_version,
        nvcc_found=nvcc_found,
        nvcc_version=nvcc_version,
        nvcc_cuda_matches_torch=nvcc_matches,
        nvcc_source=nvcc_source,
        gcc_found=shutil.which("gcc") is not None,
        python_h_found=_python_h_found(),
        sageattention_version=_package_version("sageattention"),
        sageattn3_version=_package_version("sageattn3"),
        sparge_version=_package_version("spas_sage_attn"),
        triton_version=_package_version("triton"),
        flash_attn_version=_package_version("flash_attn"),
        xformers_version=_package_version("xformers"),
        active_backend=attention.get_attention_backend(),
        available_backends=attention.available_backends(),
    )
