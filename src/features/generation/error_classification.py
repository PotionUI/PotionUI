from __future__ import annotations

import errno
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

_CUDA_OOM_MARKERS = ("cuda out of memory", "hip out of memory")

_MODEL_FILE_EXTENSIONS = (
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx",
)

_MISSING_FILE_MARKERS = ("no such file or directory", "file not found", "does not exist")

_DISK_FULL_MARKERS = ("no space left on device",)

_CORRUPT_WEIGHTS_MARKERS = (
    "headertoolarge",
    "invalid header",
    "header too large",
    "error while deserializing header",
)

_AUTH_REQUIRED_MARKERS = ("401 unauthorized", "403 forbidden", "http 401", "http 403")

_BACKEND_UNREACHABLE_MARKERS = (
    "worker unreachable",
    "connection refused",
    "could not reach",
    "failed to establish a new connection",
)

UNCLASSIFIED = "unclassified"

_CATEGORIES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "cuda_oom": (
        "Ran out of GPU memory (VRAM) during generation.",
        (
            "Lower the resolution one tier",
            "Reduce the frame count (video presets only)",
            "Switch to an fp8 or smaller model variant",
            "Close other applications using the GPU",
        ),
    ),
    "host_ram_oom": (
        "Ran out of host RAM while streaming model weights.",
        (
            "Switch to a smaller model variant",
            "Try a different model family with a lighter memory footprint",
        ),
    ),
    "missing_model_file": (
        "A model file this preset needs is missing.",
        (
            "Re-download the model from Models -> Downloads",
            "Check the file wasn't moved or deleted on disk",
        ),
    ),
    "disk_full": (
        "The disk is full.",
        (
            "Free up space on the drive backing your models/output directories",
            "Remove old outputs or unused model checkpoints",
        ),
    ),
    "corrupt_weights": (
        "A model file appears corrupted or incomplete.",
        (
            "Delete the local copy and re-download it from Models -> Downloads",
            "The download may have been interrupted - try again",
        ),
    ),
    "auth_required": (
        "The source requires credentials.",
        ("Add or refresh the provider's credentials in Administration -> Plugins",),
    ),
    "backend_unreachable": (
        "Could not reach the configured backend.",
        (
            "Check the backend is running and reachable",
            "Verify the backend's URL/port in Administration -> Backends",
        ),
    ),
    UNCLASSIFIED: (
        "Something went wrong while generating.",
        (
            "Try again",
            "If it keeps failing, send the error ID to your administrator",
        ),
    ),
}

ERROR_CATEGORIES: Tuple[str, ...] = tuple(_CATEGORIES)


@dataclass
class ErrorClassification:
    category: str
    summary: str
    suggestions: List[str] = field(default_factory=list)


def classification_for_code(code: Optional[str]) -> ErrorClassification:
    category = code if code in _CATEGORIES else UNCLASSIFIED
    summary, suggestions = _CATEGORIES[category]
    return ErrorClassification(category=category, summary=summary, suggestions=list(suggestions))


def classify_generation_error(exc: BaseException) -> ErrorClassification:
    for category, matches in _EXCEPTION_CHECKS:
        if matches(exc):
            return classification_for_code(category)
    return classify_error_text(str(exc))


def classify_error_text(text: Optional[str]) -> ErrorClassification:
    message = (text or "").lower()
    for category, matches in _TEXT_CHECKS:
        if matches(message):
            return classification_for_code(category)
    return classification_for_code(UNCLASSIFIED)


def _has_any(message: str, markers: Tuple[str, ...]) -> bool:
    return any(marker in message for marker in markers)


def _text_is_missing_model_file(message: str) -> bool:
    return _has_any(message, _MISSING_FILE_MARKERS) and _has_any(message, _MODEL_FILE_EXTENSIONS)


_TEXT_CHECKS: Tuple[Tuple[str, Callable[[str], bool]], ...] = (
    ("cuda_oom", lambda m: _has_any(m, _CUDA_OOM_MARKERS)),
    ("missing_model_file", _text_is_missing_model_file),
    ("disk_full", lambda m: _has_any(m, _DISK_FULL_MARKERS)),
    ("corrupt_weights", lambda m: _has_any(m, _CORRUPT_WEIGHTS_MARKERS)),
    ("auth_required", lambda m: _has_any(m, _AUTH_REQUIRED_MARKERS)),
    ("backend_unreachable", lambda m: _has_any(m, _BACKEND_UNREACHABLE_MARKERS)),
)


def _is_cuda_oom(exc: BaseException) -> bool:
    import torch

    return isinstance(exc, torch.cuda.OutOfMemoryError)


def _is_host_ram_exhausted(exc: BaseException) -> bool:
    from src.platform.runtime.native.errors import HostMemoryExhaustedError

    return isinstance(exc, HostMemoryExhaustedError)


def _is_missing_model_file(exc: BaseException) -> bool:
    return isinstance(exc, FileNotFoundError) and _has_any(str(exc).lower(), _MODEL_FILE_EXTENSIONS)


def _is_disk_full(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno == errno.ENOSPC


def _is_corrupt_weights(exc: BaseException) -> bool:
    try:
        import safetensors
    except ImportError:
        return False
    return isinstance(exc, safetensors.SafetensorError)


def _is_auth_required(exc: BaseException) -> bool:
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)


def _is_backend_unreachable(exc: BaseException) -> bool:
    from src.features.remote_execution.transport import WorkerUnreachableError

    if isinstance(exc, (WorkerUnreachableError, ConnectionError)):
        return True
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))


_EXCEPTION_CHECKS: Tuple[Tuple[str, Callable[[BaseException], bool]], ...] = (
    ("cuda_oom", _is_cuda_oom),
    ("host_ram_oom", _is_host_ram_exhausted),
    ("missing_model_file", _is_missing_model_file),
    ("disk_full", _is_disk_full),
    ("corrupt_weights", _is_corrupt_weights),
    ("auth_required", _is_auth_required),
    ("backend_unreachable", _is_backend_unreachable),
)
