from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from src.platform.runtime import cuda_allocator
from src.platform.runtime.native.kernels import fp8_linear, fp8_ops, nvfp4_linear

from .._nvfp4_ref import F4_MAX, F8_MAX, quantize_nvfp4

DEVICE_NOT_READY = "CUDA driver error: device not ready"


def _fp8_layer():
    lin = fp8_ops.Linear(32, 16, bias=False)
    scale = torch.tensor(0.01)
    weight = (torch.randn(16, 32) * 0.05 / scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    lin.load_state_dict({"weight": weight, "weight_scale": scale}, strict=False, assign=True)
    return lin


def _nvfp4_layer():
    lin = fp8_ops.Linear(32, 16, bias=False)
    weight = torch.randn(16, 32) * 0.05
    global_scale = (weight.abs().amax() / (F4_MAX * F8_MAX)).clamp(min=1e-8).detach()
    packed, block_scale, _, _ = quantize_nvfp4(weight, global_scale)
    lin._load_from_state_dict(
        {"weight": packed, "weight_scale": block_scale, "weight_scale_2": global_scale.clone()},
        "", {}, True, [], [], [],
    )
    return lin


def _fp8_gemm(exc):
    lin = _fp8_layer()
    with patch("torch._scaled_mm", side_effect=exc):
        return lin._forward_scaled_mm(torch.randn(1, 32, dtype=torch.bfloat16))


def _fp8_staging(exc):
    lin = _fp8_layer()
    with patch.object(fp8_linear, "cast_to", side_effect=exc):
        return lin._stage_scaled_mm_weight("cuda:0")


def _nvfp4_gemm(exc):
    lin = _nvfp4_layer()
    with patch("torch.nn.functional.scaled_mm", side_effect=exc):
        return lin._forward_nvfp4_scaled_mm(torch.randn(1, 32, dtype=torch.bfloat16))


def _nvfp4_activation_quant(exc):
    lin = _nvfp4_layer()
    with patch.object(nvfp4_linear, "quantize_nvfp4_dynamic", side_effect=exc):
        return lin._forward_nvfp4_scaled_mm(torch.randn(1, 32, dtype=torch.bfloat16))


FAST_PATHS = [_fp8_gemm, _fp8_staging, _nvfp4_gemm, _nvfp4_activation_quant]
fast_paths = pytest.mark.parametrize("fast_path", FAST_PATHS, ids=[path.__name__ for path in FAST_PATHS])


@pytest.fixture
def allocator(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    monkeypatch.setattr(cuda_allocator, "_stop_growing_expandable_segments", lambda: None)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    return monkeypatch


@fast_paths
def test_a_fatal_driver_oom_is_not_swallowed_by_the_fallback_to_dequant(fast_path, allocator):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    exc = RuntimeError(DEVICE_NOT_READY)

    with pytest.raises(RuntimeError) as caught:
        fast_path(exc)

    assert caught.value is exc
    assert cuda_allocator.allocator_damage() == DEVICE_NOT_READY


@fast_paths
def test_a_driver_oom_still_falls_back_to_dequant_where_the_policy_retries(fast_path, allocator):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "retry")

    assert fast_path(RuntimeError(DEVICE_NOT_READY)) is None
    assert cuda_allocator.allocator_damage() is None


@fast_paths
@pytest.mark.parametrize(
    "exc",
    [RuntimeError("unsupported operands"), torch.cuda.OutOfMemoryError("CUDA out of memory"), RuntimeError("CUDA driver error: invalid argument")],
    ids=["rejected_operands", "torch_oom", "other_driver_error"],
)
def test_everything_else_still_falls_back_to_dequant_under_the_fatal_policy(fast_path, allocator, exc):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")

    assert fast_path(exc) is None
    assert cuda_allocator.allocator_damage() is None
