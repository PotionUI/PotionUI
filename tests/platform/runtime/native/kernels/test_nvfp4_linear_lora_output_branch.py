from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.lora.key_mapping import LoraDelta
from src.platform.runtime.native.io.quant_formats import dequantize_nvfp4
from src.platform.runtime.native.kernels import fp8_ops, lora_math, nvfp4_linear
from src.platform.runtime.native.kernels.lora_math import _deltas_output_branch_ok, _lora_output_branch
from src.platform.runtime.native.kernels.nvfp4_linear import _nvfp4_scaled_mm_fast_path_ok

from .._nvfp4_ref import F4_MAX as _F4_MAX_REF
from .._nvfp4_ref import F8_MAX as _F8_MAX_REF
from .._nvfp4_ref import quantize_nvfp4 as _ref_quantize_nvfp4

_ATOL, _RTOL = 2e-2, 2e-2


def _nvfp4_layer(in_f=32, out_f=16, bias=False, seed=40):
    lin = fp8_ops.Linear(in_f, out_f, bias=bias)
    torch.manual_seed(seed)
    w = torch.randn(out_f, in_f) * 0.05
    pts = (w.abs().amax() / (_F4_MAX_REF * _F8_MAX_REF)).clamp(min=1e-8).detach()
    packed, block_sw, _, _ = _ref_quantize_nvfp4(w, pts)
    sd = {"weight": packed, "weight_scale": block_sw, "weight_scale_2": pts.clone()}
    if bias:
        sd["bias"] = torch.randn(out_f)
    lin._load_from_state_dict(sd, "", {}, True, [], [], [])
    return lin


def _fake_scaled_mm_dequant_reference(lin: "fp8_ops.Linear"):

    def _fake(mat_a, mat_b, scale_a, recipe_a, scale_b, recipe_b, **kwargs):
        weight = dequantize_nvfp4(
            lin.nvfp4_packed, lin.nvfp4_weight_block_scale,
            lin.nvfp4_weight_global_scale, lin.out_features, lin.in_features,
        )
        out_dtype = kwargs["output_dtype"]
        x32 = _fake.last_x2d.to(torch.float32)
        out = x32 @ weight.to(torch.float32).t()
        if kwargs.get("bias") is not None:
            out = out + kwargs["bias"].to(torch.float32)
        return out.to(out_dtype)

    return _fake


def _run_emulated_gemm_path(lin, x):
    fake = _fake_scaled_mm_dequant_reference(lin)
    orig_shape = x.shape
    fake.last_x2d = x.reshape(-1, orig_shape[-1])
    with patch("torch.nn.functional.scaled_mm", side_effect=fake):
        out = lin._forward_nvfp4_scaled_mm(x)
    assert out is not None, "emulated GEMM path unexpectedly bailed"
    return out


def _dequant_path_output(lin, x):
    return lin.forward_comfy_cast_weights(x)


def test_output_branch_matches_dequant_path_single_delta():
    lin = _nvfp4_layer(in_f=32, out_f=16, bias=True)
    torch.manual_seed(101)
    down = torch.randn(4, 32) * 0.1
    up = torch.randn(16, 4) * 0.1
    lin.lora_deltas = [LoraDelta(down=down, up=up, alpha=8.0, scale=1.0)]

    x = torch.randn(2, 5, 32, dtype=torch.bfloat16)
    gemm_out = _run_emulated_gemm_path(lin, x)
    dequant_out = _dequant_path_output(lin, x)

    assert gemm_out.shape == dequant_out.shape
    assert torch.allclose(gemm_out, dequant_out, atol=_ATOL, rtol=_RTOL)


def test_output_branch_matches_dequant_path_no_bias():
    lin = _nvfp4_layer(in_f=64, out_f=32, bias=False)
    torch.manual_seed(102)
    down = torch.randn(8, 64) * 0.1
    up = torch.randn(32, 8) * 0.1
    lin.lora_deltas = [LoraDelta(down=down, up=up, alpha=4.0, scale=0.7)]

    x = torch.randn(3, 64, dtype=torch.bfloat16)
    gemm_out = _run_emulated_gemm_path(lin, x)
    dequant_out = _dequant_path_output(lin, x)
    assert torch.allclose(gemm_out, dequant_out, atol=_ATOL, rtol=_RTOL)


def test_output_branch_matches_dequant_path_stacked_deltas():
    lin = _nvfp4_layer(in_f=32, out_f=16, bias=True)
    torch.manual_seed(103)
    deltas = [
        LoraDelta(down=torch.randn(4, 32) * 0.1, up=torch.randn(16, 4) * 0.1, alpha=8.0, scale=1.0),
        LoraDelta(down=torch.randn(6, 32) * 0.1, up=torch.randn(16, 6) * 0.1, alpha=6.0, scale=0.5),
        LoraDelta(down=torch.randn(2, 32) * 0.1, up=torch.randn(16, 2) * 0.1, alpha=2.0, scale=1.3),
    ]
    lin.lora_deltas = deltas

    x = torch.randn(4, 32, dtype=torch.bfloat16)
    gemm_out = _run_emulated_gemm_path(lin, x)
    dequant_out = _dequant_path_output(lin, x)
    assert torch.allclose(gemm_out, dequant_out, atol=_ATOL, rtol=_RTOL)


def test_predicate_rejects_lokr_delta():
    d = LoraDelta(down=torch.randn(4, 8), up=torch.randn(4, 8), alpha=1.0, scale=1.0, kron=True)
    assert _deltas_output_branch_ok([d], out_features=32) is False
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=[d], input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    ) is False


def test_predicate_rejects_out_of_bounds_target_slice_delta():
    d = LoraDelta(down=torch.randn(4, 32), up=torch.randn(8, 4), alpha=1.0, scale=1.0, target_slice=(0, 28, 8))
    assert _deltas_output_branch_ok([d], out_features=32) is False
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=[d], input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    ) is False


def test_lokr_delta_falls_back_gemm_helper_not_called():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    w1 = torch.randn(4, 4) * 0.1
    w2 = torch.randn(4, 8) * 0.1
    lin.lora_deltas = [LoraDelta(down=w2, up=w1, alpha=1.0, scale=1.0, kron=True)]

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(lin, "_forward_nvfp4_scaled_mm") as mock_gemm:
        out = lin.forward_comfy_cast_weights(x)
    mock_gemm.assert_not_called()
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


def test_target_slice_delta_falls_back_gemm_helper_not_called():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    lin.lora_deltas = [
        LoraDelta(down=torch.randn(4, 32) * 0.1, up=torch.randn(8, 4) * 0.1,
                  alpha=1.0, scale=1.0, target_slice=(0, 8, 8)),
    ]

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(lin, "_forward_nvfp4_scaled_mm") as mock_gemm:
        out = lin.forward_comfy_cast_weights(x)
    mock_gemm.assert_not_called()
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


def test_no_deltas_gate_off_uses_dequant_path():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not be called")):
        out = lin.forward_comfy_cast_weights(x)
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


def test_no_deltas_gate_on_uses_emulated_gemm_path():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    fake = _fake_scaled_mm_dequant_reference(lin)
    fake.last_x2d = x.reshape(-1, 32)
    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=fake):
        out = lin.forward_comfy_cast_weights(x)
    dequant_out = _dequant_path_output(lin, x)
    assert torch.allclose(out, dequant_out, atol=_ATOL, rtol=_RTOL)


def _stacked_deltas(rank_pattern, in_f, out_f, seed):
    torch.manual_seed(seed)
    return [
        LoraDelta(
            down=torch.randn(rank, in_f) * 0.1,
            up=torch.randn(out_f, rank) * 0.1,
            alpha=float(rank),
            scale=0.3 + 0.1 * i,
        )
        for i, rank in enumerate(rank_pattern)
    ]


def test_chunked_matches_single_chunk_reference_nonaligned_boundary():
    in_f, out_f, m = 32, 16, 37
    deltas = _stacked_deltas([4, 6, 2], in_f, out_f, seed=201)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)

    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", 16 * out_f * 2):
        chunked = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * 2 * 4):
        single_chunk = _lora_output_branch(x, deltas, torch.bfloat16, out_f)

    assert chunked.shape == (m, out_f)
    assert torch.equal(chunked, single_chunk)


def test_chunked_matches_single_chunk_reference_large_token_count():
    in_f, out_f, m = 32, 16, 4001
    deltas = _stacked_deltas([4, 8], in_f, out_f, seed=202)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)

    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", 128 * out_f * 2):
        chunked = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * 2 * 4):
        single_chunk = _lora_output_branch(x, deltas, torch.bfloat16, out_f)

    assert torch.equal(chunked, single_chunk)


def test_chunk_row_count_matches_byte_budget_formula():
    in_f, out_f, m = 32, 16, 100
    deltas = _stacked_deltas([4], in_f, out_f, seed=203)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)
    budget = 1
    itemsize = torch.tensor([], dtype=torch.bfloat16).element_size()
    expected_chunk_rows = max(1, budget // (out_f * itemsize))
    assert expected_chunk_rows == 1

    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", budget):
        out = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(lora_math, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * itemsize * 4):
        expected = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    assert torch.equal(out, expected)


def test_output_branch_matches_dequant_path_many_tokens():
    lin = _nvfp4_layer(in_f=32, out_f=16, bias=True)
    torch.manual_seed(204)
    down = torch.randn(4, 32) * 0.1
    up = torch.randn(16, 4) * 0.1
    lin.lora_deltas = [LoraDelta(down=down, up=up, alpha=8.0, scale=1.0)]

    x = torch.randn(1, 3000, 32, dtype=torch.bfloat16)
    gemm_out = _run_emulated_gemm_path(lin, x)
    dequant_out = _dequant_path_output(lin, x)

    assert gemm_out.shape == dequant_out.shape
    assert torch.allclose(gemm_out, dequant_out, atol=_ATOL, rtol=_RTOL)
