"""Classifying a generation-ending exception into an actionable category."""

import errno

import httpx
import safetensors
import torch

from src.features.generation.error_classification import classify_generation_error
from src.features.remote_execution.transport import WorkerUnreachableError
from src.platform.runtime.native.errors import HostMemoryExhaustedError


def test_a_real_cuda_oom_classifies_as_cuda_oom():
    exc = torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "cuda_oom"
    assert classification.suggestions


def test_a_cuda_oom_rewrapped_as_a_plain_runtime_error_still_classifies():
    """A pipe can catch the original torch exception deep in a call stack and
    re-raise a plain RuntimeError - the type is gone, but torch's own wording
    survives in the message. The classifier must still catch it."""
    exc = RuntimeError("CUDA out of memory. Tried to allocate 512.00 MiB (GPU 0; 23.99 GiB total)")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "cuda_oom"


def test_host_memory_exhausted_classifies_as_host_ram_oom():
    exc = HostMemoryExhaustedError("partial-residency streaming needs ~40.0GB pinned host RAM but only 8.0GB is free")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "host_ram_oom"
    assert classification.suggestions


def test_a_missing_safetensors_file_classifies_as_missing_model_file():
    exc = FileNotFoundError("[Errno 2] No such file or directory: '/models/checkpoints/sdxl/model.safetensors'")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "missing_model_file"
    assert classification.suggestions


def test_a_missing_file_without_a_model_extension_does_not_classify_as_missing_model_file():
    """Conservative: a FileNotFoundError for a config/JSON file is not
    necessarily a missing model weight."""
    exc = FileNotFoundError("[Errno 2] No such file or directory: '/models/checkpoints/sdxl/config.json'")

    classification = classify_generation_error(exc)

    assert classification is None or classification.category != "missing_model_file"


def test_disk_full_via_errno_classifies_as_disk_full():
    exc = OSError(errno.ENOSPC, "No space left on device")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "disk_full"
    assert classification.suggestions


def test_disk_full_via_message_classifies_as_disk_full():
    exc = OSError("write failed: No space left on device")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "disk_full"


def test_a_safetensors_error_classifies_as_corrupt_weights():
    exc = safetensors.SafetensorError("Error while deserializing header: header too large")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "corrupt_weights"
    assert classification.suggestions


def test_an_http_401_classifies_as_auth_required():
    request = httpx.Request("GET", "https://example.invalid/api/model")
    response = httpx.Response(401, request=request)
    exc = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "auth_required"
    assert classification.suggestions


def test_an_http_403_classifies_as_auth_required():
    request = httpx.Request("GET", "https://example.invalid/api/model")
    response = httpx.Response(403, request=request)
    exc = httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "auth_required"


def test_a_worker_unreachable_error_classifies_as_backend_unreachable():
    exc = WorkerUnreachableError("could not reach worker at http://127.0.0.1:8099: connection refused")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "backend_unreachable"
    assert classification.suggestions


def test_an_unrelated_exception_falls_back_to_a_neutral_classification():
    exc = ValueError("preset form is missing a required field")

    classification = classify_generation_error(exc)

    assert classification is not None
    assert classification.category == "unclassified"
    assert classification.summary == "Something went wrong during generation."
    # the raw exception text must never leak into the neutral headline
    assert str(exc) not in classification.summary


def test_an_exception_that_already_carries_its_own_detail_is_left_unclassified():
    """`GenerationExecutionError`-style exceptions attach a `.detail` the
    raiser deliberately curated - the neutral fallback must not paper over
    it with a generic message."""
    exc = ValueError("preset form is missing a required field")
    exc.detail = "Node 12 (KSampler): CUDA error"

    classification = classify_generation_error(exc)

    assert classification is None
