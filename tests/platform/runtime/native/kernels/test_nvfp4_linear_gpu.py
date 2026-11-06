from __future__ import annotations

from collections import Counter

import pytest
import torch
from torch.profiler import ProfilerActivity, profile

from src.platform.runtime.native.io.quant_formats import dequantize_nvfp4, quantize_nvfp4_dynamic
from src.platform.runtime.native.kernels import fp8_ops, gemm_support, row_chunks
from src.platform.settings.runtime_flags import runtime_flag_values

pytestmark = pytest.mark.requires_gpu


@pytest.fixture(autouse=True)
def _nvfp4_gemm(monkeypatch):
    if not torch.cuda.is_available() or not gemm_support().nvfp4:
        pytest.skip("needs a GPU with the nvfp4 block-scaled GEMM")
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", True)


def _layer(in_f, out_f, bias, dtype, seed=0):
    g = torch.Generator(device="cuda").manual_seed(seed)
    w = torch.randn(out_f, in_f, device="cuda", generator=g) * 0.05
    packed, block, scale = quantize_nvfp4_dynamic(w)
    lin = fp8_ops.Linear(in_f, out_f, bias=bias, dtype=dtype, device="cuda")
    sd = {"weight": packed, "weight_scale": block, "weight_scale_2": scale}
    if bias:
        sd["bias"] = (torch.randn(out_f, device="cuda", generator=g) * 4.0).to(dtype)
    lin._load_from_state_dict(sd, "", {}, True, [], [], [])
    return lin


def _reference(lin, x2d):
    m, k = x2d.shape
    packed, block, scale = quantize_nvfp4_dynamic(x2d)
    a = dequantize_nvfp4(packed, block, scale, m, k).double()
    w = dequantize_nvfp4(
        lin.nvfp4_packed, lin.nvfp4_weight_block_scale, lin.nvfp4_weight_global_scale, lin.out_features, k,
    ).double()
    exact = a @ w.t()
    magnitude = a.abs() @ w.abs().t()
    if lin.bias is not None:
        bias = lin.bias.to(x2d.dtype).double()
        exact = exact + bias
        magnitude = magnitude + bias.abs()
    return exact, magnitude


def _assert_within_one_rounding(out, lin, x2d):
    exact, magnitude = _reference(lin, x2d)
    error = (out.double() - exact).abs()
    allowed = torch.finfo(x2d.dtype).eps * exact.abs() + 2.0 ** -20 * magnitude
    assert out.dtype == x2d.dtype
    assert bool((error <= allowed).all()), float((error - allowed).max())


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float16])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("rows", [1, 7, 300])
def test_the_fast_path_lands_within_one_output_rounding_of_the_exact_product(dtype, bias, rows):
    lin = _layer(3072, 1536, bias, dtype, seed=rows)
    x = (torch.randn(rows, 3072, device="cuda") * 2).to(dtype)
    with torch.inference_mode():
        out = lin._forward_nvfp4_scaled_mm(x)
    assert out is not None
    _assert_within_one_rounding(out, lin, x)


def test_row_chunks_each_get_the_bias_inside_the_gemm(monkeypatch):
    lin = _layer(1024, 512, True, torch.bfloat16, seed=5)
    x = (torch.randn(640, 1024, device="cuda") * 2).to(torch.bfloat16)
    monkeypatch.setattr(row_chunks, "QUANT_CHUNK_ELEMENTS", 256 * 1024)
    with torch.inference_mode():
        out = lin._forward_nvfp4_scaled_mm(x)
    assert out is not None
    _assert_within_one_rounding(out, lin, x)


def _gpu_and_op_names(fn) -> Counter:
    def names(f):
        f()
        torch.cuda.synchronize()
        with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
            f()
        return Counter(e.name for e in prof.events())

    return names(fn) - names(lambda: None)


@pytest.mark.parametrize("rows", [1, 300])
def test_a_biased_linear_runs_no_separate_add_after_the_gemm(rows):
    lin = _layer(3072, 1536, True, torch.bfloat16)
    x = torch.randn(rows, 3072, device="cuda").to(torch.bfloat16)
    with torch.inference_mode():
        names = _gpu_and_op_names(lambda: lin(x))
    gemms = sum(n for name, n in names.items() if "gemm_block_scaled" in name)
    assert gemms == 1
    assert names["aten::_scaled_mm_v2"] == 1
    assert names["aten::add"] == 0
    assert names["aten::add_"] == 0
