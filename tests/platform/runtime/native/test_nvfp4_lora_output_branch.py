"""nvfp4 native GEMM fast path with an output-side LoRA branch.

CPU-only: ``torch._scaled_mm_v2``/``F.scaled_mm`` is a real Blackwell (sm120+)
kernel, so every test here monkeypatches ``torch.nn.functional.scaled_mm``
with a reference that dequantises via ``dequantize_nvfp4`` and matmuls in
fp32 -- exactly what the real kernel computes, minus the fp4 tensor cores.
This lets ``_forward_nvfp4_scaled_mm`` run for real (including its own
``_lora_output_branch`` accumulation) and be compared against the
existing dequant + ``apply_lora_deltas`` forward path.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.lora.key_mapping import LoraDelta
from vendor.gpl.comfyui import ops as _ops_module
from vendor.gpl.comfyui.ops import (
    _deltas_output_branch_ok,
    _lora_output_branch,
    _nvfp4_scaled_mm_fast_path_ok,
    dequantize_nvfp4,
    fp8_ops,
)

from ._nvfp4_ref import F4_MAX as _F4_MAX_REF
from ._nvfp4_ref import F8_MAX as _F8_MAX_REF
from ._nvfp4_ref import quantize_nvfp4 as _ref_quantize_nvfp4

# bf16 has ~3 decimal digits of precision; the reference and branch paths
# round at different points (weight-side delta rounded to bf16 before the
# matmul vs. an fp32 accumulate rounded to bf16 once at the end), so a plain
# torch.allclose at bf16's own tolerance is the right bar, not bit-exactness.
_ATOL, _RTOL = 2e-2, 2e-2


def _nvfp4_layer(in_f=32, out_f=16, bias=False, seed=40):
    lin = fp8_ops.Linear(in_f, out_f, bias=bias)  # Nvfp4Linear
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
    """A ``F.scaled_mm`` stand-in that reproduces the real kernel's result via
    ``dequantize_nvfp4`` + an fp32 matmul (no LoRA -- the base GEMM only).
    Captures the layer's weight at call time so ``mat_a``/output_dtype from
    the real call site drive shape/dtype."""

    def _fake(mat_a, mat_b, scale_a, recipe_a, scale_b, recipe_b, **kwargs):
        weight = dequantize_nvfp4(
            lin.nvfp4_packed, lin.nvfp4_weight_block_scale,
            lin.nvfp4_weight_global_scale, lin.out_features, lin.in_features,
        )
        out_dtype = kwargs["output_dtype"]
        # mat_a is the packed nvfp4 activation; recover M from its shape and
        # replay the matmul directly against the ORIGINAL fp32 input instead
        # of unpacking mat_a -- the base-GEMM numerics under test are the
        # kernel's dequant contract (already covered by test_nvfp4.py), not
        # the activation quantiser.
        x32 = _fake.last_x2d.to(torch.float32)
        return (x32 @ weight.to(torch.float32).t()).to(out_dtype)

    return _fake


def _run_emulated_gemm_path(lin, x):
    """Forward ``x`` through ``lin._forward_nvfp4_scaled_mm`` with
    ``F.scaled_mm`` monkeypatched to the fp32-dequant reference above."""
    fake = _fake_scaled_mm_dequant_reference(lin)
    orig_shape = x.shape
    fake.last_x2d = x.reshape(-1, orig_shape[-1])
    with patch("torch.nn.functional.scaled_mm", side_effect=fake):
        out = lin._forward_nvfp4_scaled_mm(x)
    assert out is not None, "emulated GEMM path unexpectedly bailed"
    return out


def _dequant_path_output(lin, x):
    """Reference: the existing dequant + apply_lora_deltas forward, run
    directly (bypasses the $NATIVE_NVFP4_MATMUL gate entirely)."""
    return lin.forward_comfy_cast_weights(x)


# --- (a) plain LoRA: GEMM-path branch == dequant path -----------------------


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


# --- (b) multiple stacked deltas at different strengths compose identically -


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


# --- (c)/(d) ineligible deltas: predicate rejects, GEMM helper not called ---


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
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    w1 = torch.randn(4, 4) * 0.1  # kron(w1, w2) -> (16, 32), matches (out, in)
    w2 = torch.randn(4, 8) * 0.1
    lin.lora_deltas = [LoraDelta(down=w2, up=w1, alpha=1.0, scale=1.0, kron=True)]

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(lin, "_forward_nvfp4_scaled_mm") as mock_gemm:
        out = lin.forward_comfy_cast_weights(x)
    mock_gemm.assert_not_called()
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


def test_target_slice_delta_falls_back_gemm_helper_not_called():
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    lin.lora_deltas = [
        LoraDelta(down=torch.randn(4, 32) * 0.1, up=torch.randn(8, 4) * 0.1,
                  alpha=1.0, scale=1.0, target_slice=(0, 8, 8)),
    ]

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(lin, "_forward_nvfp4_scaled_mm") as mock_gemm:
        out = lin.forward_comfy_cast_weights(x)
    mock_gemm.assert_not_called()
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


# --- (e) no deltas: gate behaves exactly as before this change --------------


def test_no_deltas_gate_off_uses_dequant_path():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not be called")):
        out = lin.forward_comfy_cast_weights(x)  # $NATIVE_NVFP4_MATMUL off by default
    expected = _dequant_path_output(lin, x)
    assert torch.equal(out, expected)


def test_no_deltas_gate_on_uses_emulated_gemm_path():
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    fake = _fake_scaled_mm_dequant_reference(lin)
    fake.last_x2d = x.reshape(-1, 32)
    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=fake):
        out = lin.forward_comfy_cast_weights(x)
    dequant_out = _dequant_path_output(lin, x)
    assert torch.allclose(out, dequant_out, atol=_ATOL, rtol=_RTOL)


# --- bounded-memory chunking (post-OOM-fix) ---------------------------------
#
# Row-chunking a matmul is per-row independent (no cross-row reduction), so a
# chunked run and a single-chunk run of _lora_output_branch must be
# bit-exact, not just close-within-tolerance -- that bit-exactness is what
# these tests check, which is a stronger and more direct guarantee than
# comparing against the (looser-tolerance) dequant reference used above.


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
    # Force chunk_rows small enough that a modest M crosses several chunk
    # boundaries, including a final partial chunk (M not a multiple of it).
    in_f, out_f, m = 32, 16, 37
    deltas = _stacked_deltas([4, 6, 2], in_f, out_f, seed=201)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)

    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", 16 * out_f * 2):
        chunked = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * 2 * 4):
        single_chunk = _lora_output_branch(x, deltas, torch.bfloat16, out_f)

    assert chunked.shape == (m, out_f)
    assert torch.equal(chunked, single_chunk)


def test_chunked_matches_single_chunk_reference_large_token_count():
    # A "huge-token" stand-in for a 2x-resolution stage-2 video batch: M far
    # larger than a single forced-small chunk, several chunks execute, and
    # the result is still exactly what one big unchunked pass would produce.
    in_f, out_f, m = 32, 16, 4001
    deltas = _stacked_deltas([4, 8], in_f, out_f, seed=202)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)

    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", 128 * out_f * 2):
        chunked = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * 2 * 4):
        single_chunk = _lora_output_branch(x, deltas, torch.bfloat16, out_f)

    assert torch.equal(chunked, single_chunk)


def test_chunk_row_count_matches_byte_budget_formula():
    # Structural: chunk_rows is derived as budget // (out_features * itemsize).
    # Confirm the number of `torch.Tensor.new_empty` allocations attributable
    # to this call (the one full-size (M, out_features) output buffer) never
    # grows because of chunking, and separately confirm the chosen chunk_rows
    # keeps a (chunk_rows, out_features) bf16 buffer within the target budget.
    in_f, out_f, m = 32, 16, 100
    deltas = _stacked_deltas([4], in_f, out_f, seed=203)
    x = torch.randn(m, in_f, dtype=torch.bfloat16)
    budget = 1  # bytes -- deliberately tiny to force chunk_rows == 1
    itemsize = torch.tensor([], dtype=torch.bfloat16).element_size()
    expected_chunk_rows = max(1, budget // (out_f * itemsize))
    assert expected_chunk_rows == 1

    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", budget):
        out = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    with patch.object(_ops_module, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", m * out_f * itemsize * 4):
        expected = _lora_output_branch(x, deltas, torch.bfloat16, out_f)
    assert torch.equal(out, expected)


def test_output_branch_matches_dequant_path_many_tokens():
    # End-to-end (through the real GEMM-path forward, default chunk budget):
    # a token count large enough to exercise multiple chunks under the
    # production _NVFP4_LORA_BRANCH_CHUNK_BYTES default still agrees with the
    # dequant fallback within bf16 tolerance.
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
