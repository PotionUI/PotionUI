from __future__ import annotations

from unittest.mock import patch

import pytest
import torch

from src.platform.runtime.native.kernels import fp8_linear, row_chunks
from src.platform.runtime.native.kernels.fp8_linear import (
    _quantize_fp8,
    _quantize_fp8_dynamic,
    fp8_activation_scale,
    reset_scaled_mm_fast_path_rejection_log,
)

from .test_fp8_linear_scale_preflight import _fake_scaled_mm_math, _fp8_layer

_IN, _OUT = 64, 32


@pytest.fixture(autouse=True)
def _fresh_rejection_log():
    reset_scaled_mm_fast_path_rejection_log()
    yield
    reset_scaled_mm_fast_path_rejection_log()


def _layer(input_scale=None):
    torch.manual_seed(5)
    weight = (torch.randn(_OUT, _IN) * 40).clamp(-448, 448).to(torch.float8_e4m3fn)
    return _fp8_layer(torch.tensor(0.01), input_scale=input_scale, bias=torch.randn(_OUT),
                      in_f=_IN, out_f=_OUT, weight=weight)


def _activations(rows, seed=9):
    torch.manual_seed(seed)
    x = torch.randn(rows, _IN) * 3.0
    x[rows - 3, 11] = -61.0
    return x.to(torch.bfloat16)


def _forward(lin, x, *, chunk_elements, oom_above_rows=None):
    calls = []
    real = _quantize_fp8

    def quantize(x2d, scale):
        calls.append(x2d.shape[0])
        if oom_above_rows is not None and x2d.shape[0] > oom_above_rows:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 8.40 GiB")
        return real(x2d, scale)

    with patch.object(row_chunks, "QUANT_CHUNK_ELEMENTS", chunk_elements), \
         patch.object(fp8_linear, "_quantize_fp8", side_effect=quantize), \
         patch("torch._scaled_mm", _fake_scaled_mm_math):
        return lin._forward_scaled_mm(x), calls


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_the_vector_norm_scale_is_the_scale_the_old_abs_amax_quantiser_used():
    x = _activations(640)
    old = x.abs().amax().clamp(min=1e-8).to(torch.float32) / 448.0
    assert torch.equal(fp8_activation_scale(x), old)
    assert torch.equal(fp8_activation_scale(torch.zeros(4, _IN, dtype=torch.bfloat16)),
                       torch.zeros((), dtype=torch.bfloat16).clamp(min=1e-8).to(torch.float32) / 448.0)


def test_quantising_row_chunks_against_the_whole_input_scale_is_bit_exact():
    x = _activations(640)
    whole, scale = _quantize_fp8_dynamic(x)
    parts = [_quantize_fp8(x[i:i + 128], scale) for i in range(0, 640, 128)]
    assert torch.equal(torch.cat(parts).view(torch.uint8), whole.view(torch.uint8))


@pytest.mark.parametrize("input_scale", [None, torch.tensor(0.05)], ids=["dynamic", "static"])
def test_a_chunked_fp8_forward_matches_the_unchunked_forward_bit_for_bit(input_scale):
    lin = _layer(input_scale)
    x = _activations(640).reshape(2, 320, _IN)
    whole, whole_calls = _forward(lin, x, chunk_elements=1 << 20)
    chunked, chunked_calls = _forward(lin, x, chunk_elements=256 * _IN)
    assert whole_calls == [640]
    assert chunked_calls == [256, 256, 128]
    assert chunked.shape == (2, 320, _OUT)
    assert torch.equal(chunked, whole)


def test_an_fp8_quant_oom_retries_in_smaller_chunks_instead_of_raising(caplog):
    lin = _layer()
    x = _activations(1024)
    expected, _ = _forward(lin, x, chunk_elements=1 << 20)
    with caplog.at_level("WARNING"):
        out, calls = _forward(lin, x, chunk_elements=1 << 20, oom_above_rows=256)
    assert torch.equal(out, expected)
    assert calls == [1024, 512, 256, 256, 256, 256]
    assert _warnings(caplog) == []


def test_an_fp8_quant_oom_at_the_smallest_chunk_falls_back_with_a_warning(caplog):
    lin = _layer()
    with caplog.at_level("WARNING"):
        out, calls = _forward(lin, _activations(512), chunk_elements=1 << 20, oom_above_rows=0)
    assert out is None
    assert calls == [512, 256, 128]
    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "activation quant failed (OutOfMemoryError)" in warnings[0]


def test_a_non_oom_fp8_quant_error_still_raises():
    lin = _layer()
    with patch.object(fp8_linear, "_quantize_fp8", side_effect=ValueError("bad activations")), \
         patch("torch._scaled_mm", _fake_scaled_mm_math), pytest.raises(ValueError):
        lin._forward_scaled_mm(_activations(64))
