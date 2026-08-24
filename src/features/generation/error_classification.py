"""Classifies a generation-ending exception into a short, user-facing
category with concrete remediation, without discarding the original error.

`classify_generation_error()` recognizes a fixed set of unambiguous failure
signatures (CUDA/host-RAM exhaustion, missing or corrupt model weights, a
full disk, a backend requiring credentials, an unreachable backend). An
exception that doesn't match any of those but also carries no `.detail` of
its own (i.e. the caller would otherwise show raw `str(exc)` as the
headline) gets a neutral fallback classification instead. An exception that
already attaches a curated `.detail` (e.g. `GenerationExecutionError` from a
pipe/backend) returns None so the caller keeps using its deliberately
written message untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

_CUDA_OOM_MARKERS = ("CUDA out of memory", "HIP out of memory")

# Matched by substring, not exception type, because a pipe can catch the
# original torch.cuda.OutOfMemoryError deep in a call stack and re-raise it
# wrapped in a plain RuntimeError - the type is gone by the time it reaches
# GenerationManager, but torch's own wording survives in str(exc).
_VRAM_SUGGESTIONS = (
    "Lower the resolution one tier",
    "Reduce the frame count (video presets only)",
    "Switch to an fp8 or smaller model variant",
    "Close other applications using the GPU",
)

_HOST_RAM_SUGGESTIONS = (
    "Switch to a smaller model variant",
    "Try a different model family with a lighter memory footprint",
)

_MODEL_FILE_EXTENSIONS = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx",
)

_MISSING_MODEL_SUGGESTIONS = (
    "Re-download the model from Models -> Downloads",
    "Check the file wasn't moved or deleted on disk",
)

_DISK_FULL_SUGGESTIONS = (
    "Free up space on the drive backing your models/output directories",
    "Remove old outputs or unused model checkpoints",
)

_CORRUPT_WEIGHTS_MARKERS = (
    "headertoolarge",
    "invalid header",
    "header too large",
    "error while deserializing header",
)

_CORRUPT_WEIGHTS_SUGGESTIONS = (
    "Delete the local copy and re-download it from Models -> Downloads",
    "The download may have been interrupted - try again",
)

_AUTH_REQUIRED_MARKERS = ("401 unauthorized", "403 forbidden", "http 401", "http 403")

_AUTH_REQUIRED_SUGGESTIONS = (
    "Add or refresh the provider's credentials in Administration -> Plugins",
)

_BACKEND_UNREACHABLE_SUGGESTIONS = (
    "Check the backend is running and reachable",
    "Verify the backend's URL/port in Administration -> Backends",
)

_NEUTRAL_SUGGESTIONS = (
    "Check the details below for the underlying error",
)


@dataclass
class ErrorClassification:
    # "cuda_oom" | "host_ram_oom" | "missing_model_file" | "disk_full" |
    # "corrupt_weights" | "auth_required" | "backend_unreachable" |
    # "unclassified"
    category: str
    summary: str
    suggestions: List[str] = field(default_factory=list)


def classify_generation_error(exc: BaseException) -> Optional[ErrorClassification]:
    if _is_cuda_oom(exc):
        return ErrorClassification(
            category="cuda_oom",
            summary="Ran out of GPU memory (VRAM) during generation.",
            suggestions=list(_VRAM_SUGGESTIONS),
        )
    if _is_host_ram_exhausted(exc):
        return ErrorClassification(
            category="host_ram_oom",
            summary="Ran out of host RAM while streaming model weights.",
            suggestions=list(_HOST_RAM_SUGGESTIONS),
        )
    if _is_missing_model_file(exc):
        return ErrorClassification(
            category="missing_model_file",
            summary="A model file this preset needs is missing.",
            suggestions=list(_MISSING_MODEL_SUGGESTIONS),
        )
    if _is_disk_full(exc):
        return ErrorClassification(
            category="disk_full",
            summary="The disk is full.",
            suggestions=list(_DISK_FULL_SUGGESTIONS),
        )
    if _is_corrupt_weights(exc):
        return ErrorClassification(
            category="corrupt_weights",
            summary="A model file appears corrupted or incomplete.",
            suggestions=list(_CORRUPT_WEIGHTS_SUGGESTIONS),
        )
    if _is_auth_required(exc):
        return ErrorClassification(
            category="auth_required",
            summary="The source requires credentials.",
            suggestions=list(_AUTH_REQUIRED_SUGGESTIONS),
        )
    if _is_backend_unreachable(exc):
        return ErrorClassification(
            category="backend_unreachable",
            summary="Could not reach the configured backend.",
            suggestions=list(_BACKEND_UNREACHABLE_SUGGESTIONS),
        )
    if getattr(exc, "detail", None):
        return None
    return ErrorClassification(
        category="unclassified",
        summary="Something went wrong during generation.",
        suggestions=list(_NEUTRAL_SUGGESTIONS),
    )


def _is_cuda_oom(exc: BaseException) -> bool:
    # Deferred import: `torch` (and, below, `src.platform.runtime.native`)
    # sit on GenerationManager's import chain, which bootstrap.app must stay
    # free of at process boot (tests/architecture/test_boot_imports.py) -
    # both only load once a generation actually fails, not at import time.
    import torch

    if isinstance(exc, torch.cuda.OutOfMemoryError):
        return True
    return any(marker in str(exc) for marker in _CUDA_OOM_MARKERS)


def _is_host_ram_exhausted(exc: BaseException) -> bool:
    from src.platform.runtime.native.errors import HostMemoryExhaustedError

    return isinstance(exc, HostMemoryExhaustedError)


def _is_missing_model_file(exc: BaseException) -> bool:
    if not isinstance(exc, FileNotFoundError):
        return False
    message = str(exc).lower()
    return any(ext in message for ext in _MODEL_FILE_EXTENSIONS)


def _is_disk_full(exc: BaseException) -> bool:
    import errno

    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return True
    return "no space left on device" in str(exc).lower()


def _is_corrupt_weights(exc: BaseException) -> bool:
    try:
        import safetensors
    except ImportError:
        pass
    else:
        if isinstance(exc, safetensors.SafetensorError):
            return True
    message = str(exc).lower()
    return any(marker in message for marker in _CORRUPT_WEIGHTS_MARKERS)


def _is_auth_required(exc: BaseException) -> bool:
    try:
        import httpx
    except ImportError:
        pass
    else:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in (401, 403)
    message = str(exc).lower()
    return any(marker in message for marker in _AUTH_REQUIRED_MARKERS)


def _is_backend_unreachable(exc: BaseException) -> bool:
    from src.features.remote_execution.transport import WorkerUnreachableError

    if isinstance(exc, WorkerUnreachableError):
        return True
    if isinstance(exc, ConnectionError):
        return True
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
