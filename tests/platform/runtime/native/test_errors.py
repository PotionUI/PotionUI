"""Native engine error classes: message shape, forensic attribute wiring."""

from __future__ import annotations

import torch

from src.platform.runtime import cuda_allocator
from src.platform.runtime.native.errors import (
    PoisonedConditioningError,
    SamplingNumericsError,
    is_cuda_oom,
    retry_after_cuda_oom,
)


def test_sampling_numerics_error_default_message_unchanged():
    err = SamplingNumericsError(4, "euler", "sage2")
    assert str(err) == "numerical instability (NaN/Inf) at step 4 (sampler=euler, attention=sage2)"
    assert err.tensor_name is None
    assert err.switch_step is None


def test_sampling_numerics_error_tensor_name_and_switch_step_in_message():
    err = SamplingNumericsError(3, "unipc", "sage2", tensor_name="x0", switch_step=2)
    msg = str(err)
    assert "tensor=x0" in msg
    assert "expert=low, switch_step=2" in msg


def test_sampling_numerics_error_switch_step_active_expert_before_switch():
    err = SamplingNumericsError(1, "unipc", switch_step=2)
    assert "expert=high" in str(err)


def test_annotate_segment_appends_to_message_and_sets_attributes():
    err = SamplingNumericsError(3, "unipc", "sage2", tensor_name="x0", switch_step=2)
    err.annotate_segment(1, "chain/i2v")
    assert err.segment_index == 1
    assert err.segment_label == "chain/i2v"
    assert str(err).endswith("segment 1 (chain/i2v)")


def test_annotate_segment_without_label():
    err = SamplingNumericsError(0, "euler")
    err.annotate_segment(2)
    assert str(err).endswith("segment 2")
    assert err.segment_label is None


def test_poisoned_conditioning_error_message():
    err = PoisonedConditioningError("cond", "context")
    assert "cond" in str(err)
    assert "context" in str(err)


def test_is_cuda_oom_recognises_the_torch_out_of_memory_error():
    assert is_cuda_oom(torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"))


def test_is_cuda_oom_recognises_an_accelerator_error_that_says_out_of_memory():
    assert is_cuda_oom(torch.AcceleratorError("CUDA error: out of memory"))


def test_is_cuda_oom_rejects_an_accelerator_error_that_does_not_say_out_of_memory():
    assert not is_cuda_oom(torch.AcceleratorError("CUDA error: an illegal memory access was encountered"))
    assert not is_cuda_oom(torch.AcceleratorError("CUDA error: CUBLAS_STATUS_EXECUTION_FAILED when calling `cublasGemmEx`"))


def test_is_cuda_oom_rejects_a_plain_runtime_error_even_when_it_says_out_of_memory():
    assert not is_cuda_oom(RuntimeError("CUDA error: out of memory"))


def test_is_cuda_oom_recognises_the_wsl_allocator_failure(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "retry")

    assert is_cuda_oom(RuntimeError("CUDA driver error: device not ready"))


def test_is_cuda_oom_recognises_a_driver_error_that_says_out_of_memory(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "retry")

    assert is_cuda_oom(RuntimeError("CUDA driver error: out of memory"))


def test_is_cuda_oom_recognises_the_wsl_allocator_failure_with_a_cpp_stack_appended(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "retry")

    assert is_cuda_oom(RuntimeError("CUDA driver error: device not ready\nException raised from map at c10/cuda/CUDACachingAllocator.cpp"))


def test_is_cuda_oom_rejects_other_driver_errors():
    assert not is_cuda_oom(RuntimeError("CUDA driver error: invalid argument"))
    assert not is_cuda_oom(RuntimeError("CUDA driver error: an illegal memory access was encountered"))
    assert not is_cuda_oom(RuntimeError("CUDA driver error: unknown error"))


def test_is_cuda_oom_rejects_the_wsl_message_on_an_error_that_is_not_a_runtime_error():
    assert not is_cuda_oom(ValueError("CUDA driver error: device not ready"))
    assert not is_cuda_oom(OSError("CUDA driver error: device not ready"))


def test_is_cuda_oom_accepts_the_wsl_driver_oom_under_the_retry_policy(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "retry")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)

    assert is_cuda_oom(RuntimeError("CUDA driver error: device not ready"))
    assert is_cuda_oom(RuntimeError("CUDA driver error: out of memory"))
    assert cuda_allocator.allocator_damage() is None


def test_is_cuda_oom_stops_retrying_a_driver_oom_under_wsl_by_default(monkeypatch):
    monkeypatch.delenv("POTIONUI_CUDA_DRIVER_OOM", raising=False)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: True)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)

    assert not is_cuda_oom(RuntimeError("CUDA driver error: device not ready"))
    assert is_cuda_oom(torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert is_cuda_oom(torch.AcceleratorError("CUDA error: out of memory"))
    assert cuda_allocator.allocator_damage() is None


def test_is_cuda_oom_retries_a_driver_oom_on_native_linux_by_default(monkeypatch):
    monkeypatch.delenv("POTIONUI_CUDA_DRIVER_OOM", raising=False)
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: False)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)

    assert retry_after_cuda_oom(RuntimeError("CUDA driver error: device not ready"))
    assert cuda_allocator.allocator_damage() is None


def test_a_driver_oom_raised_as_an_accelerator_error_still_follows_the_policy(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)

    assert not is_cuda_oom(torch.AcceleratorError("CUDA driver error: out of memory"))


def test_a_fallback_records_the_driver_oom_it_will_not_retry(monkeypatch):
    stopped = []
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: stopped.append(True))

    assert retry_after_cuda_oom(torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"))
    assert cuda_allocator.allocator_damage() is None
    assert not retry_after_cuda_oom(RuntimeError("CUDA driver error: device not ready"))
    assert cuda_allocator.allocator_damage() == "CUDA driver error: device not ready"
    assert stopped == [True]


def test_a_fallback_under_the_retry_policy_retries_a_driver_oom_and_records_nothing(monkeypatch):
    monkeypatch.setenv("POTIONUI_CUDA_DRIVER_OOM", "retry")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    monkeypatch.setattr(cuda_allocator, "_damage", None)

    assert retry_after_cuda_oom(RuntimeError("CUDA driver error: device not ready"))
    assert cuda_allocator.allocator_damage() is None
