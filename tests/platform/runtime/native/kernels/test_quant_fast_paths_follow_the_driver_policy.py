from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import torch

from src.platform.runtime import cuda_allocator
from src.platform.runtime.native.kernels import fp8_linear, fp8_ops, nvfp4_linear, row_chunks

from .._nvfp4_ref import F4_MAX, F8_MAX, quantize_nvfp4
from .test_fp8_linear_scale_preflight import _fake_scaled_mm_math

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
    with patch.object(nvfp4_linear, "quantize_nvfp4_activation", side_effect=exc):
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


def _fp8_activation_quant(exc):
    lin = _fp8_layer()
    with patch.object(fp8_linear, "_quantize_fp8", side_effect=exc):
        return lin._forward_scaled_mm(torch.randn(1, 32, dtype=torch.bfloat16))


def _chunked_fp8(module, name, replacement):
    lin = _fp8_layer()
    with patch.object(row_chunks, "QUANT_CHUNK_ELEMENTS", 128 * 32), \
         patch.object(module, name, replacement), \
         patch("torch._scaled_mm", _fake_scaled_mm_math):
        return lin._forward_scaled_mm(torch.randn(512, 32, dtype=torch.bfloat16))


def _chunk_scale(exc):
    return _chunked_fp8(fp8_linear, "fp8_activation_scale", MagicMock(side_effect=exc))


def _chunk_output(exc):
    return _chunked_fp8(row_chunks, "torch", SimpleNamespace(empty=MagicMock(side_effect=exc)))


CHUNKED_PATHS = [_fp8_activation_quant, _chunk_scale, _chunk_output]
chunked_paths = pytest.mark.parametrize("fast_path", CHUNKED_PATHS, ids=[path.__name__ for path in CHUNKED_PATHS])


@chunked_paths
def test_a_fatal_driver_oom_in_the_row_chunk_driver_is_not_swallowed(fast_path, allocator):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "fatal")
    exc = RuntimeError(DEVICE_NOT_READY)

    with pytest.raises(RuntimeError) as caught:
        fast_path(exc)

    assert caught.value is exc
    assert cuda_allocator.allocator_damage() == DEVICE_NOT_READY


@chunked_paths
@pytest.mark.parametrize("policy", ["fatal", "retry"])
def test_a_retried_oom_in_the_row_chunk_driver_falls_back_to_dequant(fast_path, allocator, policy):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, policy)
    exc = torch.cuda.OutOfMemoryError("CUDA out of memory") if policy == "fatal" else RuntimeError(DEVICE_NOT_READY)

    assert fast_path(exc) is None
    assert cuda_allocator.allocator_damage() is None


def test_a_driver_oom_the_policy_retries_halves_the_quant_chunk_like_any_oom(allocator):
    allocator.setenv(cuda_allocator.DRIVER_OOM_POLICY_ENV, "retry")
    seen = []
    real = fp8_linear._quantize_fp8

    def quantize(rows, scale):
        seen.append(rows.shape[0])
        if rows.shape[0] > 128:
            raise RuntimeError(DEVICE_NOT_READY)
        return real(rows, scale)

    lin = _fp8_layer()
    with patch.object(row_chunks, "QUANT_CHUNK_ELEMENTS", 256 * 32), \
         patch.object(fp8_linear, "_quantize_fp8", side_effect=quantize), \
         patch("torch._scaled_mm", _fake_scaled_mm_math):
        out = lin._forward_scaled_mm(torch.randn(512, 32, dtype=torch.bfloat16))

    assert out is not None and out.shape == (512, 16)
    assert seen == [256, 128, 128, 128, 128]
