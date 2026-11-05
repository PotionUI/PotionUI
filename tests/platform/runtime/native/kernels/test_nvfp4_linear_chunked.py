from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from src.platform.runtime.native.io.quant_formats import (
    dequantize_nvfp4,
    nvfp4_activation_amax,
    quantize_nvfp4_dynamic,
)
from src.platform.runtime.native.kernels import (
    QUANT_TILE_ROWS,
    activation_quant_chunk_rows,
    nvfp4_linear,
    quantize_nvfp4_activation,
    row_chunks,
)
from src.platform.runtime.native.kernels.nvfp4_linear import reset_nvfp4_fast_path_rejection_log

from .test_nvfp4_linear_matmul import _nvfp4_layer

_IN, _OUT = 64, 32


@pytest.fixture(autouse=True)
def _fresh_rejection_log():
    reset_nvfp4_fast_path_rejection_log()
    yield
    reset_nvfp4_fast_path_rejection_log()


def _reference_scaled_mm(mat_a, mat_b, scale_a, _recipe_a, scale_b, _recipe_b, **kwargs):
    rows, half_k = mat_a.shape
    k = half_k * 2
    cols = mat_b.shape[1]
    a = dequantize_nvfp4(mat_a.view(torch.uint8), scale_a[0], scale_a[1], rows, k)
    b = dequantize_nvfp4(mat_b.t().view(torch.uint8), scale_b[0], scale_b[1], cols, k)
    out = a.double() @ b.double().t()
    if kwargs.get("bias") is not None:
        out = out + kwargs["bias"].double()
    return out.to(kwargs["output_dtype"])


def _activations(rows, seed=3):
    torch.manual_seed(seed)
    x = torch.randn(rows, _IN) * 2.0
    x[rows // 2, 7] = 37.0
    return x.to(torch.bfloat16)


def _quant_calls(oom_above_rows=None):
    calls = []
    real = quantize_nvfp4_activation

    def quantize(x2d, tensor_amax=None):
        calls.append(x2d.shape[0])
        if oom_above_rows is not None and x2d.shape[0] > oom_above_rows:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 5.32 GiB")
        return real(x2d, tensor_amax=tensor_amax)

    return calls, quantize


def _forward(lin, x, *, chunk_elements, oom_above_rows=None, scaled_mm=_reference_scaled_mm):
    calls, quantize = _quant_calls(oom_above_rows)
    with patch.object(row_chunks, "QUANT_CHUNK_ELEMENTS", chunk_elements), \
         patch.object(nvfp4_linear, "quantize_nvfp4_activation", side_effect=quantize), \
         patch("torch.nn.functional.scaled_mm", side_effect=scaled_mm):
        return lin._forward_nvfp4_scaled_mm(x), calls


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_chunk_rows_bound_the_quantiser_scratch_and_stay_on_the_scale_tile_grid():
    assert activation_quant_chunk_rows(16384) == 4096
    assert activation_quant_chunk_rows(4096) == 16384
    assert activation_quant_chunk_rows(5120) % QUANT_TILE_ROWS == 0
    assert activation_quant_chunk_rows(5120) * 5120 <= row_chunks.QUANT_CHUNK_ELEMENTS
    assert activation_quant_chunk_rows(1 << 30) == QUANT_TILE_ROWS


def test_the_global_amax_is_the_amax_the_whole_tensor_quantiser_uses():
    x = _activations(640)
    blocks = x.float().reshape(640, -1, 16).abs().amax(-1)
    assert torch.equal(nvfp4_activation_amax(x).float(), blocks.amax())


def test_quantising_row_chunks_against_the_global_amax_is_bit_exact():
    x = _activations(640)
    packed, block, scale = quantize_nvfp4_dynamic(x)
    amax = nvfp4_activation_amax(x)
    parts = [quantize_nvfp4_dynamic(x[i:i + 128], tensor_amax=amax) for i in range(0, 640, 128)]
    assert torch.equal(torch.cat([p[0] for p in parts]), packed)
    assert torch.equal(torch.cat([p[1] for p in parts]).view(torch.uint8), block.view(torch.uint8))
    assert all(torch.equal(p[2], scale) for p in parts)


def test_a_chunked_forward_matches_the_unchunked_forward_bit_for_bit():
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT, bias=True)
    x = _activations(640).reshape(2, 320, _IN)
    whole, whole_calls = _forward(lin, x, chunk_elements=1 << 20)
    chunked, chunked_calls = _forward(lin, x, chunk_elements=256 * _IN)
    assert whole_calls == [640]
    assert chunked_calls == [256, 256, 128]
    assert chunked.shape == (2, 320, _OUT)
    assert torch.equal(chunked, whole)


def test_every_chunk_gets_the_bias_from_the_gemm_exactly_once():
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT, bias=True)
    x = _activations(640)
    chunked, calls = _forward(lin, x, chunk_elements=256 * _IN)
    packed, block, scale = quantize_nvfp4_dynamic(x)
    a = dequantize_nvfp4(packed, block, scale, 640, _IN).double()
    w = dequantize_nvfp4(
        lin.nvfp4_packed, lin.nvfp4_weight_block_scale, lin.nvfp4_weight_global_scale, _OUT, _IN,
    ).double()
    expected = (a @ w.t() + lin.bias.to(torch.bfloat16).double()).to(torch.bfloat16)
    assert calls == [256, 256, 128]
    assert torch.equal(chunked, expected)


def test_an_oom_on_a_large_input_retries_in_smaller_chunks_instead_of_dequantising(caplog):
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    x = _activations(1024)
    expected, _ = _forward(lin, x, chunk_elements=1 << 20)
    with caplog.at_level("WARNING"):
        out, calls = _forward(lin, x, chunk_elements=1 << 20, oom_above_rows=256)
    assert out is not None and torch.equal(out, expected)
    assert calls[0] == 1024 and calls[1] == 512
    assert calls[2:] == [256, 256, 256, 256]
    assert _warnings(caplog) == []


def test_an_oom_that_survives_the_smallest_chunk_falls_back_for_that_call_only(caplog):
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    x = _activations(512)
    with caplog.at_level("WARNING"):
        failed, calls = _forward(lin, x, chunk_elements=1 << 20, oom_above_rows=0)
        recovered, _ = _forward(lin, x, chunk_elements=1 << 20)
    assert failed is None
    assert calls == [512, 256, 128]
    assert recovered is not None and recovered.shape == (512, _OUT)
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "activation quant failed (OutOfMemoryError)" in warnings[0]


def test_a_non_oom_quantiser_error_falls_back_without_retrying():
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    calls = []

    def broken(x2d, tensor_amax=None):
        calls.append(x2d.shape[0])
        raise ValueError("bad activations")

    with patch.object(nvfp4_linear, "quantize_nvfp4_activation", side_effect=broken), \
         patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")):
        assert lin._forward_nvfp4_scaled_mm(_activations(512)) is None
    assert calls == [512]


def test_an_output_buffer_oom_falls_back_after_one_quant_call_without_halving(caplog):
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    x = _activations(1024)
    real_empty = torch.empty
    out_allocs = []

    def empty(*args, **kwargs):
        if tuple(args[0]) == (1024, _OUT):
            out_allocs.append(args[0])
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (output)")
        return real_empty(*args, **kwargs)

    with caplog.at_level("WARNING"), patch("torch.empty", side_effect=empty):
        out, calls = _forward(lin, x, chunk_elements=1 << 20, oom_above_rows=1023)
    assert out is None
    assert calls == [1024] and len(out_allocs) == 1
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "output allocation failed (OutOfMemoryError)" in warnings[0]


def test_a_scaled_mm_oom_retries_once_at_half_the_rows(caplog):
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    x = _activations(1024)
    expected, _ = _forward(lin, x, chunk_elements=1 << 20)
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(args[0].shape[0])
        if len(attempts) == 1:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (gemm)")
        return _reference_scaled_mm(*args, **kwargs)

    with caplog.at_level("WARNING"):
        out, calls = _forward(lin, x, chunk_elements=1 << 20, scaled_mm=flaky)
    assert calls == [1024, 512, 512]
    assert attempts == [1024, 512, 512]
    assert torch.equal(out, expected)
    assert _warnings(caplog) == []


def test_a_failed_activation_amax_never_retries_smaller_chunks(caplog):
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    amax_calls = []

    def failing_amax(x2d):
        amax_calls.append(x2d.shape[0])
        raise torch.cuda.OutOfMemoryError("CUDA out of memory (amax)")

    with caplog.at_level("WARNING"), patch.object(nvfp4_linear, "nvfp4_activation_amax", side_effect=failing_amax):
        out, calls = _forward(lin, _activations(1024), chunk_elements=256 * _IN)
    assert out is None
    assert amax_calls == [1024] and calls == []
    assert "activation scale failed (OutOfMemoryError)" in _warnings(caplog)[0]


def test_the_activation_amax_is_computed_once_across_oom_retries():
    lin = _nvfp4_layer(in_f=_IN, out_f=_OUT)
    amax_calls = []
    real = nvfp4_linear.nvfp4_activation_amax

    def counting_amax(x2d):
        amax_calls.append(x2d.shape[0])
        return real(x2d)

    with patch.object(nvfp4_linear, "nvfp4_activation_amax", side_effect=counting_amax):
        out, calls = _forward(lin, _activations(1024), chunk_elements=512 * _IN, oom_above_rows=128)
    assert out is not None
    assert calls[0] == 512 and calls[1] == 256 and calls[2] == 128
    assert amax_calls == [1024]
