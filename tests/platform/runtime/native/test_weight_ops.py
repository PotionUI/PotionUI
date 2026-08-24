"""Tests for the ops namespaces: manual cast, scaled fp8, pick_operations."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
import torch.nn as nn
import torch.nn.functional as F

from src.platform.runtime.native.ops.dtype import pick_dtypes
from vendor.gpl.comfyui.ops import (
    NATIVE_FP8_MATMUL_ENV,
    NATIVE_NVFP4_MATMUL_ENV,
    QUANT_FP8_SCALED,
    _fp8_matmul_enabled,
    _nvfp4_fast_path_reject_reason,
    _nvfp4_matmul_enabled,
    _nvfp4_scaled_mm_fast_path_ok,
    _nvfp4_scaled_mm_supported,
    _quantize_fp8_dynamic,
    _quantize_nvfp4_dynamic,
    _scaled_mm_fast_path_ok,
    _scaled_mm_fast_path_reject_reason,
    _scaled_mm_supported,
    detect_quant_format,
    disable_weight_init,
    dequantize_nvfp4,
    fp8_ops,
    manual_cast,
    pick_operations,
    reset_nvfp4_fast_path_rejection_log,
    reset_nvfp4_scaled_mm_probe,
    reset_scaled_mm_fast_path_rejection_log,
    reset_scaled_mm_probe,
)

from ._nvfp4_ref import F4_MAX as _F4_MAX_REF  # noqa: E402
from ._nvfp4_ref import F8_MAX as _F8_MAX_REF  # noqa: E402
from ._nvfp4_ref import quantize_nvfp4 as _ref_quantize_nvfp4  # noqa: E402


def test_disable_weight_init_skips_init_and_runs():
    lin = disable_weight_init.Linear(4, 3)
    # reset_parameters is a no-op; weights are whatever load provides.
    lin.weight = nn.Parameter(torch.eye(3, 4))
    lin.bias = nn.Parameter(torch.zeros(3))
    out = lin(torch.ones(1, 4))
    assert out.shape == (1, 3)


def test_manual_cast_linear_fp16_storage_fp32_compute():
    lin = manual_cast.Linear(4, 3, bias=True)
    lin.weight = nn.Parameter(torch.randn(3, 4, dtype=torch.float16))
    lin.bias = nn.Parameter(torch.randn(3, dtype=torch.float16))
    x = torch.randn(2, 4, dtype=torch.float32)
    out = lin(x)
    assert out.dtype == torch.float32
    ref = F.linear(x, lin.weight.float(), lin.bias.float())
    assert torch.allclose(out, ref, atol=1e-3)


def test_manual_cast_stack_two_layers():
    net = nn.Sequential(
        manual_cast.Linear(4, 8, bias=False),
        manual_cast.Linear(8, 2, bias=False),
    )
    for m in net:
        m.weight = nn.Parameter(torch.randn(m.out_features, m.in_features, dtype=torch.float16))
    x = torch.randn(3, 4, dtype=torch.float32)
    out = net(x)
    assert out.shape == (3, 2)
    assert out.dtype == torch.float32


def test_fp8_scaled_linear_load_and_forward():
    lin = fp8_ops.Linear(4, 3, bias=False)
    real_w = torch.randn(3, 4)
    scale = torch.tensor(0.5)
    w_fp8 = (real_w / scale).to(torch.float8_e4m3fn)
    sd = {"weight": w_fp8, "weight_scale": scale, "input_scale": torch.tensor(1.0)}

    missing, unexpected = lin.load_state_dict(sd, strict=False, assign=True)
    # scale keys are consumed, not flagged unexpected.
    assert missing == []
    assert unexpected == []
    assert lin.weight_scale is not None
    assert lin.input_scale is not None

    x = torch.randn(2, 4, dtype=torch.bfloat16)
    out = lin(x)
    ref = F.linear(x, w_fp8.to(torch.bfloat16) * scale.to(torch.bfloat16))
    assert out.dtype == torch.bfloat16
    assert torch.allclose(out, ref)


def test_fp8_scaled_linear_legacy_scale_weight_spelling():
    # T5-style scaled_fp8 uses `<layer>.scale_weight` instead of `.weight_scale`.
    lin = fp8_ops.Linear(4, 3, bias=False)
    real_w = torch.randn(3, 4)
    scale = torch.tensor(0.25)
    w_fp8 = (real_w / scale).to(torch.float8_e4m3fn)
    missing, unexpected = lin.load_state_dict(
        {"weight": w_fp8, "scale_weight": scale}, strict=False, assign=True,
    )
    assert missing == [] and unexpected == []
    assert lin.weight_scale is not None
    x = torch.randn(2, 4, dtype=torch.bfloat16)
    ref = F.linear(x, w_fp8.to(torch.bfloat16) * scale.to(torch.bfloat16))
    assert torch.allclose(lin(x), ref)


def test_is_mixed_precision():
    from src.platform.runtime.native.ops.dtype import is_mixed_precision
    # Krea-2 style: bf16 block Linears + f32 norm/peripheral weights.
    mixed = {
        "blocks.0.attn.wq.weight": torch.zeros(4, dtype=torch.bfloat16),
        "first.weight": torch.zeros(4, dtype=torch.float32),
    }
    assert is_mixed_precision(mixed) is True
    # single-dtype (Klein bf16) -> not mixed.
    single = {"a": torch.zeros(4, dtype=torch.bfloat16), "b": torch.zeros(4, dtype=torch.bfloat16)}
    assert is_mixed_precision(single) is False
    # fp8 weights + f32 scale sidecars must NOT read as mixed (scales excluded).
    fp8 = {
        "l.weight": torch.zeros(4, dtype=torch.float8_e4m3fn),
        "l.weight_scale": torch.zeros((), dtype=torch.float32),
        "l.input_scale": torch.zeros((), dtype=torch.float32),
    }
    assert is_mixed_precision(fp8) is False


def test_detect_quant_format():
    # `_quantization_metadata` alone is not enough: it must be backed by at
    # least one tensor actually stored in a quantised dtype in this sd.
    assert detect_quant_format(
        {"_quantization_metadata": "{}"},
        {"blk.0.weight": torch.zeros(2, dtype=torch.float8_e4m3fn)},
    ) == QUANT_FP8_SCALED
    assert detect_quant_format({}, {"scaled_fp8": torch.zeros(0)}) == QUANT_FP8_SCALED
    assert detect_quant_format({}, {"blk.0.weight_scale": torch.zeros(())}) == QUANT_FP8_SCALED
    assert detect_quant_format({}, {"blk.0.scale_weight": torch.zeros(())}) == QUANT_FP8_SCALED
    assert detect_quant_format({"format": "pt"}, {"blk.0.weight": torch.zeros(2)}) is None


def test_detect_quant_format_ignores_file_level_marker_for_bf16_component():
    """Regression for the LTX all-in-one checkpoint bug: the DiT's
    `_quantization_metadata` header describes only `model.diffusion_model.*`
    layers, but `load_torch_file_prefixed` returns the whole file's
    `__metadata__` block regardless of which component's keys were sliced into
    `sd`. A bf16 vae/audio_vae/vocoder component sliced from that same file
    must NOT inherit the DiT's fp8 marker."""
    metadata = {"_quantization_metadata": '{"format_version": 3, "layers": {"model.diffusion_model.x": {}}}'}
    bf16_vae_sd = {
        "encoder.conv_in.weight": torch.zeros(4, 4, 3, 3, dtype=torch.bfloat16),
        "encoder.conv_in.bias": torch.zeros(4, dtype=torch.bfloat16),
    }
    assert detect_quant_format(metadata, bf16_vae_sd) is None

    # The DiT slice from the SAME file (real fp8 tensors) must still detect fp8.
    dit_sd = {"transformer_blocks.0.attn.to_k.weight": torch.zeros(4, 4, dtype=torch.float8_e4m3fn)}
    assert detect_quant_format(metadata, dit_sd) == QUANT_FP8_SCALED


def test_detect_quant_format_nvfp4_marker_unaffected():
    # nvfp4 layers carry real weight_scale/weight_scale_2 keys in their own sd
    # slice regardless of any file-level `_quantization_metadata` marker.
    sd = {
        "blk.0.weight": torch.zeros(4, 2, dtype=torch.uint8),
        "blk.0.weight_scale": torch.zeros(4, 1, dtype=torch.float8_e4m3fn),
        "blk.0.weight_scale_2": torch.zeros(()),
    }
    assert detect_quant_format({}, sd) == QUANT_FP8_SCALED
    assert detect_quant_format({"_quantization_metadata": "{}"}, sd) == QUANT_FP8_SCALED


def test_fp8_linear_falls_back_when_no_scale():
    # a mixed checkpoint layer stored bf16 (no weight_scale key present).
    lin = fp8_ops.Linear(4, 3, bias=False)
    lin.weight = nn.Parameter(torch.randn(3, 4, dtype=torch.bfloat16))
    x = torch.randn(1, 4, dtype=torch.bfloat16)
    out = lin(x)
    ref = F.linear(x, lin.weight)
    assert torch.allclose(out, ref)


def test_pick_operations_selection():
    assert pick_operations(torch.bfloat16, torch.bfloat16) is disable_weight_init
    assert pick_operations(torch.float16, torch.bfloat16) is manual_cast
    assert pick_operations(torch.float8_e4m3fn, torch.bfloat16) is fp8_ops
    assert pick_operations(torch.bfloat16, torch.bfloat16, QUANT_FP8_SCALED) is fp8_ops


def test_ops_namespace_protocol_surface():
    for ns in (disable_weight_init, manual_cast, fp8_ops):
        for attr in ("Linear", "Conv2d", "Conv3d", "GroupNorm", "LayerNorm", "RMSNorm", "Embedding"):
            assert hasattr(ns, attr), f"{ns.__name__} missing {attr}"


def test_manual_cast_conv2d():
    conv = manual_cast.Conv2d(2, 2, 3, padding=1, bias=True)
    conv.weight = nn.Parameter(torch.randn(2, 2, 3, 3, dtype=torch.float16))
    conv.bias = nn.Parameter(torch.zeros(2, dtype=torch.float16))
    x = torch.randn(1, 2, 4, 4, dtype=torch.float32)
    out = conv(x)
    assert out.dtype == torch.float32
    assert out.shape == (1, 2, 4, 4)


def test_pick_dtypes_fp8_keeps_storage():
    storage, compute = pick_dtypes(torch.float8_e4m3fn, "cpu")
    assert storage == torch.float8_e4m3fn
    assert compute in (torch.bfloat16, torch.float16)


def test_pick_dtypes_fp32_downcasts():
    storage, compute = pick_dtypes(torch.float32, "cpu")
    assert storage == compute


# --- native fp8 matmul (torch._scaled_mm) fast path -----------------------


@pytest.fixture(autouse=True)
def _reset_scaled_mm_probe():
    reset_scaled_mm_probe()
    yield
    reset_scaled_mm_probe()


def test_fp8_matmul_disabled_by_default(monkeypatch):
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        assert _fp8_matmul_enabled() is False


def test_fp8_matmul_env_off_never_uses_fast_path_even_if_probe_true(monkeypatch):
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "off")
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        assert _fp8_matmul_enabled() is False


def test_fp8_matmul_env_on_requires_probe(monkeypatch):
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=False):
        assert _fp8_matmul_enabled() is False
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        assert _fp8_matmul_enabled() is True


def test_fp8_matmul_env_auto_requires_probe(monkeypatch):
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "auto")
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=False):
        assert _fp8_matmul_enabled() is False
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        assert _fp8_matmul_enabled() is True


def test_fp8_matmul_unknown_policy_warns_and_disables(monkeypatch, caplog):
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "bogus")
    with caplog.at_level("WARNING"):
        assert _fp8_matmul_enabled() is False
    assert "unknown" in caplog.text.lower()


def test_scaled_mm_supported_probe_caches(monkeypatch):
    calls = {"n": 0}

    def fake_capability():
        calls["n"] += 1
        return (9, 0)

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", fake_capability)
    assert _scaled_mm_supported() is True
    assert _scaled_mm_supported() is True
    assert calls["n"] == 1  # cached after first probe


def test_scaled_mm_supported_false_below_sm89(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda: (8, 6))  # Ampere
    assert _scaled_mm_supported() is False


def test_scaled_mm_supported_false_without_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert _scaled_mm_supported() is False


def test_scaled_mm_fast_path_ok_happy_case():
    assert _scaled_mm_fast_path_ok(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=None,
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    ) is True


def test_scaled_mm_fast_path_rejects_lora_deltas():
    # LoRA-deltas present -> the dequant path stays in use (deltas apply to the
    # dequantised weight; the fp8 fast path has no delta-application seam).
    assert _scaled_mm_fast_path_ok(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=["fake-delta"],
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    ) is False


def test_scaled_mm_fast_path_rejects_cpu_input():
    assert _scaled_mm_fast_path_ok(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=None,
        input_dtype=torch.bfloat16, input_is_cuda=False, weight_is_cuda=True,
        in_features=32, out_features=32,
    ) is False


def test_scaled_mm_fast_path_rejects_unaligned_shapes():
    assert _scaled_mm_fast_path_ok(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=None,
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=30, out_features=32,  # 30 % 16 != 0
    ) is False


def test_scaled_mm_fast_path_rejects_fp32_input():
    assert _scaled_mm_fast_path_ok(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=None,
        input_dtype=torch.float32, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    ) is False


def test_scaled_mm_fast_path_reject_reason_agrees_with_ok_predicate():
    ok_kwargs = dict(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=None,
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    )
    assert _scaled_mm_fast_path_reject_reason(**ok_kwargs) is None
    assert _scaled_mm_fast_path_ok(**ok_kwargs) is True

    bad_kwargs = dict(ok_kwargs, input_dtype=torch.float32)
    reason = _scaled_mm_fast_path_reject_reason(**bad_kwargs)
    assert reason is not None and "float32" in reason
    assert _scaled_mm_fast_path_ok(**bad_kwargs) is False


def test_scaled_mm_fast_path_reject_reason_distinguishes_weight_device_dtype_and_scale():
    # Under partial residency a streamed fp8 leaf can still have its weight on
    # pinned CPU RAM at forward entry (weight_is_cuda False) even though the
    # activation is already the right dtype on the right device -- a
    # completely different fix (turn on NATIVE_STREAM_PREFETCH) than a
    # wrong-dtype weight (use a real fp8 checkpoint) or a mixed checkpoint
    # layer with no scale at all (falls back to plain manual-cast, not this
    # predicate). The four must never collapse into the same reason string.
    base = dict(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=32, out_features=32,
    )
    weight_not_cuda = _scaled_mm_fast_path_reject_reason(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True,
        **dict(base, weight_is_cuda=False),
    )
    wrong_weight_dtype = _scaled_mm_fast_path_reject_reason(
        weight_dtype=torch.float16, has_weight_scale=True, **base,
    )
    no_scale = _scaled_mm_fast_path_reject_reason(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=False, **base,
    )
    wrong_input_dtype = _scaled_mm_fast_path_reject_reason(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True,
        **dict(base, input_dtype=torch.float32),
    )
    assert weight_not_cuda == "weight_not_cuda"
    assert wrong_weight_dtype == "weight_dtype=torch.float16"
    assert no_scale == "no_weight_scale"
    assert wrong_input_dtype == "input_dtype=torch.float32"
    assert len({weight_not_cuda, wrong_weight_dtype, no_scale, wrong_input_dtype}) == 4


def test_quantize_fp8_dynamic_round_trips_within_e4m3_precision():
    x = torch.randn(4, 16) * 0.3
    x_fp8, scale = _quantize_fp8_dynamic(x)
    assert x_fp8.dtype == torch.float8_e4m3fn
    assert scale.dtype == torch.float32 and scale.ndim == 0
    x_dq = x_fp8.to(torch.float32) * scale
    torch.testing.assert_close(x_dq, x, rtol=0.15, atol=0.05)


def test_forward_scaled_mm_calls_torch_scaled_mm_with_expected_layout_and_dtypes():
    # Exercises Fp8ScaledLinear._forward_scaled_mm directly (bypassing the
    # $NATIVE_FP8_MATMUL / hardware-probe gate, which forward_comfy_cast_weights
    # already checked) — asserts the exact args passed to torch._scaled_mm:
    # fp8 dtypes on both operands, scale_a/scale_b as fp32, out_dtype ==
    # input.dtype, and the weight operand laid out COLUMN-major (K, N) — i.e.
    # its transpose is the contiguous tensor, matching self.weight.t() of a
    # row-major (N, K) nn.Linear weight.
    lin = fp8_ops.Linear(32, 16, bias=True)
    real_w = torch.randn(16, 32) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    bias = torch.randn(16)
    lin.load_state_dict(
        {"weight": w_fp8, "weight_scale": w_scale, "bias": bias}, strict=False, assign=True,
    )

    x = torch.randn(2, 3, 32, dtype=torch.bfloat16)
    fake_out = torch.zeros(6, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", return_value=fake_out) as mock_scaled_mm:
        out = lin._forward_scaled_mm(x)

    assert out.shape == (2, 3, 16)
    mock_scaled_mm.assert_called_once()
    args, kwargs = mock_scaled_mm.call_args
    a, b = args
    assert a.dtype == torch.float8_e4m3fn and a.shape == (6, 32)
    assert b.dtype == torch.float8_e4m3fn and b.shape == (32, 16)
    # column-major: b's transpose (N, K) is the contiguous tensor.
    assert b.t().is_contiguous()
    assert kwargs["scale_a"].dtype == torch.float32
    assert kwargs["scale_b"].dtype == torch.float32
    assert kwargs["out_dtype"] == torch.bfloat16
    assert kwargs["bias"] is not None and kwargs["bias"].dtype == torch.bfloat16


def test_forward_scaled_mm_uses_static_input_scale_when_present():
    lin = fp8_ops.Linear(16, 16, bias=False)
    real_w = torch.randn(16, 16) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).to(torch.float8_e4m3fn)
    lin.load_state_dict(
        {"weight": w_fp8, "weight_scale": w_scale, "input_scale": torch.tensor(0.02)},
        strict=False, assign=True,
    )
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    fake_out = torch.zeros(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", return_value=fake_out) as mock_scaled_mm:
        lin._forward_scaled_mm(x)
    _, kwargs = mock_scaled_mm.call_args
    assert torch.equal(kwargs["scale_a"], torch.tensor(0.02))


def test_scaled_mm_math_matches_existing_dequant_forward():
    # torch._scaled_mm itself is a GPU-only kernel (can't run on this CPU-only
    # test box), so this validates the SCALE PLUMBING the fast path would feed
    # it: quantise/dequantise activation + weight with the exact scales
    # _forward_scaled_mm computes, matmul in fp32, and compare against the
    # existing (already-validated) dequant forward. rtol is loose - e4m3 has
    # ~2 bits of mantissa, so this is checking the plumbing, not kernel numerics.
    lin = fp8_ops.Linear(16, 16, bias=True)
    real_w = torch.randn(16, 16) * 0.1
    w_scale = torch.tensor(0.02)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    bias = torch.randn(16)
    lin.load_state_dict(
        {"weight": w_fp8, "weight_scale": w_scale, "bias": bias}, strict=False, assign=True,
    )

    x = torch.randn(3, 16, dtype=torch.bfloat16)
    expected = lin(x)  # existing dequant path

    x2d = x.reshape(-1, 16)
    x_fp8, x_scale = _quantize_fp8_dynamic(x2d)
    x_dq = x_fp8.to(torch.float32) * x_scale
    w_dq = w_fp8.to(torch.float32) * w_scale
    simulated = F.linear(x_dq, w_dq, bias.to(torch.float32)).to(torch.bfloat16)

    torch.testing.assert_close(simulated, expected.to(torch.float32), rtol=0.1, atol=0.1, check_dtype=False)


def test_forward_comfy_cast_weights_falls_back_when_non_branchable_lora_deltas_present(monkeypatch):
    # Even with the gate fully "on" (env=on + probe forced true), a LoRA delta
    # that can't be expressed as an output-side branch (LoKr here) must keep
    # using the dequant path -- the fp8 fast path is never invoked (verified
    # by making torch._scaled_mm raise if it were called). A branch-eligible
    # (plain, full-width or sliced) delta no longer forces this fallback --
    # see test_forward_scaled_mm_lora_output_branch_matches_dequant_parity.
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        lin = fp8_ops.Linear(16, 16, bias=False)
        real_w = torch.randn(16, 16) * 0.05
        w_scale = torch.tensor(0.01)
        w_fp8 = (real_w / w_scale).to(torch.float8_e4m3fn)
        lin.load_state_dict({"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True)
        lin.lora_deltas = [LoraDelta(down=torch.randn(4, 4), up=torch.randn(4, 4), alpha=4.0, scale=1.0, kron=True)]

        x = torch.randn(1, 16, dtype=torch.bfloat16)
        with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
            out = lin(x)  # CPU input -> is_cuda False anyway, but this also
            # proves the LoKr delta alone is enough to skip it.
        assert out.shape == (1, 16)


def test_forward_comfy_cast_weights_falls_back_on_cpu_even_when_gate_on(monkeypatch):
    # forward_comfy_cast_weights on a real (CPU) module never takes the fast
    # path regardless of the env gate, because input.is_cuda/weight.is_cuda are
    # False on this test box -- torch._scaled_mm is a CUDA-only kernel.
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    with patch("vendor.gpl.comfyui.ops._scaled_mm_supported", return_value=True):
        lin = fp8_ops.Linear(16, 16, bias=False)
        real_w = torch.randn(16, 16) * 0.05
        w_scale = torch.tensor(0.01)
        w_fp8 = (real_w / w_scale).to(torch.float8_e4m3fn)
        lin.load_state_dict({"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True)

        x = torch.randn(1, 16, dtype=torch.bfloat16)
        with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
            out = lin(x)
        ref = F.linear(x, w_fp8.to(torch.bfloat16) * w_scale.to(torch.bfloat16))
        assert torch.allclose(out, ref)


# --- Codex E4-E8: _scaled_mm robustness / graceful fallback ------------------


def _fp8_layer(w_scale, *, input_scale=None, bias=False, in_f=16, out_f=16):
    """An Fp8ScaledLinear with a fp8 weight + the given (possibly bad) scales."""
    lin = fp8_ops.Linear(in_f, out_f, bias=bias)
    sd = {"weight": torch.zeros(out_f, in_f, dtype=torch.float8_e4m3fn), "weight_scale": w_scale}
    if input_scale is not None:
        sd["input_scale"] = input_scale
    if bias:
        sd["bias"] = torch.zeros(out_f)
    lin.load_state_dict(sd, strict=False, assign=True)
    return lin


def test_forward_scaled_mm_bails_on_nonscalar_weight_scale():
    # E6: a broadcastable per-output scale can't feed the scalar fast path -> fall
    # back (return None) rather than crash at reshape(()); _scaled_mm not called.
    lin = _fp8_layer(torch.full((16, 1), 0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
        assert lin._forward_scaled_mm(x) is None


def test_forward_scaled_mm_bails_on_nonscalar_input_scale():
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor([0.02, 0.03]))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
        assert lin._forward_scaled_mm(x) is None


def test_forward_scaled_mm_bails_on_zero_or_nonfinite_scales():
    # E5: a zero/non-finite calibration scale would quantize to NaN — bail instead.
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
        assert _fp8_layer(torch.tensor(0.0))._forward_scaled_mm(x) is None
        assert _fp8_layer(torch.tensor(float("inf")))._forward_scaled_mm(x) is None
        assert _fp8_layer(torch.tensor(float("nan")))._forward_scaled_mm(x) is None
        assert _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.0))._forward_scaled_mm(x) is None
        assert _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(float("inf")))._forward_scaled_mm(x) is None


def test_forward_scaled_mm_bails_on_kernel_runtime_error():
    # E8 (and E4 mixed-device, which surfaces AS a runtime rejection): a
    # _scaled_mm RuntimeError must degrade to the dequant fallback, never propagate.
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=RuntimeError("no compatible kernel image")):
        assert lin._forward_scaled_mm(x) is None


def test_forward_scaled_mm_moves_scales_and_bias_to_input_device():
    # E4: scales/bias are materialised on the input's device (a streamed layer's
    # weight may be prefetched to GPU while its scales/bias sit on CPU) so
    # _scaled_mm never sees mixed-device operands.
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02), bias=True)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    seen = {}

    def _fake(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        seen["dev"] = (a.device, scale_a.device, scale_b.device, None if bias is None else bias.device)
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype, device=a.device)

    with patch("torch._scaled_mm", _fake):
        out = lin._forward_scaled_mm(x)
    assert out is not None and out.shape == (2, 16)
    a_dev, sa_dev, sb_dev, bias_dev = seen["dev"]
    assert sa_dev == a_dev and sb_dev == a_dev and bias_dev == a_dev


def test_forward_falls_back_to_dequant_when_fast_path_bails():
    # The caller takes the dequant path (correct output) when _forward_scaled_mm
    # returns None, instead of crashing.
    import vendor.gpl.comfyui.ops as wo

    real_w = torch.randn(16, 16) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).to(torch.float8_e4m3fn)
    lin = fp8_ops.Linear(16, 16, bias=False)
    lin.load_state_dict({"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True)
    x = torch.randn(1, 16, dtype=torch.bfloat16)

    # Patch the reason function, which is what the call site actually reads --
    # patching the `_ok` predicate instead would be inert, and the test would
    # still pass on a CPU box (where the real reason is `input_not_cuda`) while
    # proving nothing about the kernel-bailed path. `assert_called_once` is what
    # keeps it that way: it fails the moment the fast branch stops being entered.
    with patch.object(wo, "_fp8_matmul_enabled", return_value=True), \
         patch.object(wo, "_scaled_mm_fast_path_reject_reason", return_value=None), \
         patch.object(lin, "_forward_scaled_mm", return_value=None) as attempted:
        out = lin.forward_comfy_cast_weights(x)
    attempted.assert_called_once()
    ref = F.linear(x, w_fp8.to(torch.bfloat16) * w_scale.to(torch.bfloat16))
    assert torch.allclose(out, ref)


# --- fp8 fast-path rejection observability -----------------------------------


@pytest.fixture(autouse=True)
def _reset_scaled_mm_fast_path_rejection_log():
    reset_scaled_mm_fast_path_rejection_log()
    yield
    reset_scaled_mm_fast_path_rejection_log()


def _loaded_fp8_layer(in_f=16, out_f=16):
    real_w = torch.randn(out_f, in_f) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.load_state_dict({"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True)
    return lin


def test_forward_comfy_cast_weights_logs_scaled_mm_fast_path_rejection_reason(caplog):
    # This used to be completely silent: gate on + predicate False fell
    # straight through to dequant with zero indication of why -- exactly how a
    # streamed fp8 leaf silently skipping the fast path on every forward went
    # unnoticed. Must fire at least once, and must say WHY.
    import vendor.gpl.comfyui.ops as wo

    lin = _loaded_fp8_layer()
    x_wrong_dtype = torch.randn(1, 16, dtype=torch.float32)  # rejects on input_dtype

    with patch.object(wo, "_fp8_matmul_enabled", return_value=True), \
         patch("torch._scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("float32" in w for w in warnings)


def test_forward_comfy_cast_weights_logs_scaled_mm_fast_path_rejection_only_once_per_reason(caplog):
    # Hard requirement: NOT per-Linear-per-forward (~350 linears x N steps
    # would flood the log) -- one line per distinct reason for the process.
    import vendor.gpl.comfyui.ops as wo

    lin = _loaded_fp8_layer()
    x_wrong_dtype = torch.randn(1, 16, dtype=torch.float32)

    with patch.object(wo, "_fp8_matmul_enabled", return_value=True), \
         patch("torch._scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        for _ in range(5):
            lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_forward_comfy_cast_weights_does_not_log_when_fast_path_eligible(caplog):
    # Force eligibility past the predicate (CPU-box tensors can never really
    # satisfy is_cuda) so this exercises the "took the fast path, no
    # rejection to log" branch instead of the always-false-on-CPU device check.
    import vendor.gpl.comfyui.ops as wo

    lin = _loaded_fp8_layer()
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    fake_out = torch.zeros(1, 16, dtype=torch.bfloat16)

    with patch.object(wo, "_fp8_matmul_enabled", return_value=True), \
         patch.object(wo, "_scaled_mm_fast_path_reject_reason", return_value=None), \
         patch("torch._scaled_mm", return_value=(fake_out, torch.zeros(1, 16))), \
         patch.object(lin, "_forward_scaled_mm", return_value=fake_out), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# --- native nvfp4 matmul (torch._scaled_mm_v2 via F.scaled_mm) fast path ---


@pytest.fixture(autouse=True)
def _reset_nvfp4_scaled_mm_probe():
    reset_nvfp4_scaled_mm_probe()
    yield
    reset_nvfp4_scaled_mm_probe()


def test_nvfp4_matmul_disabled_by_default(monkeypatch):
    monkeypatch.delenv(NATIVE_NVFP4_MATMUL_ENV, raising=False)
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=True):
        assert _nvfp4_matmul_enabled() is False


def test_nvfp4_matmul_env_off_never_uses_fast_path_even_if_probe_true(monkeypatch):
    monkeypatch.setenv(NATIVE_NVFP4_MATMUL_ENV, "off")
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=True):
        assert _nvfp4_matmul_enabled() is False


def test_nvfp4_matmul_env_on_requires_probe(monkeypatch):
    monkeypatch.setenv(NATIVE_NVFP4_MATMUL_ENV, "on")
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=False):
        assert _nvfp4_matmul_enabled() is False
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=True):
        assert _nvfp4_matmul_enabled() is True


def test_nvfp4_matmul_env_auto_requires_probe(monkeypatch):
    monkeypatch.setenv(NATIVE_NVFP4_MATMUL_ENV, "auto")
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=False):
        assert _nvfp4_matmul_enabled() is False
    with patch("vendor.gpl.comfyui.ops._nvfp4_scaled_mm_supported", return_value=True):
        assert _nvfp4_matmul_enabled() is True


def test_nvfp4_matmul_unknown_policy_warns_and_disables(monkeypatch, caplog):
    monkeypatch.setenv(NATIVE_NVFP4_MATMUL_ENV, "bogus")
    with caplog.at_level("WARNING"):
        assert _nvfp4_matmul_enabled() is False
    assert "unknown" in caplog.text.lower()


def test_nvfp4_scaled_mm_supported_probe_caches(monkeypatch):
    calls = {"n": 0}

    def fake_trial():
        calls["n"] += 1
        return True

    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda: (12, 0))
    with patch("vendor.gpl.comfyui.ops._nvfp4_trial_gemm_ok", side_effect=fake_trial):
        assert _nvfp4_scaled_mm_supported() is True
        assert _nvfp4_scaled_mm_supported() is True
    assert calls["n"] == 1  # cached after first probe


def test_nvfp4_scaled_mm_supported_false_below_sm120(monkeypatch):
    # Ada/Hopper (sm89/sm90) have fp8 tensor cores but not packed fp4 x fp4 ones.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda: (9, 0))
    with patch("vendor.gpl.comfyui.ops._nvfp4_trial_gemm_ok", side_effect=AssertionError("must not run")):
        assert _nvfp4_scaled_mm_supported() is False


def test_nvfp4_scaled_mm_supported_false_without_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert _nvfp4_scaled_mm_supported() is False


def test_nvfp4_scaled_mm_supported_false_when_trial_gemm_fails(monkeypatch):
    # sm120 capability alone is not enough -- the trial GEMM (kernel wiring,
    # not just the driver-reported SM number) has to actually succeed too.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda: (12, 0))
    with patch("vendor.gpl.comfyui.ops._nvfp4_trial_gemm_ok", return_value=False):
        assert _nvfp4_scaled_mm_supported() is False


def test_nvfp4_scaled_mm_fast_path_ok_happy_case():
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    ) is True


def test_nvfp4_scaled_mm_fast_path_rejects_lora_deltas():
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=["fake-delta"], input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    ) is False


def test_nvfp4_scaled_mm_fast_path_rejects_non_cuda_operands():
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=False, weight_is_cuda=True, in_features=64, out_features=32,
    ) is False
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=False, in_features=64, out_features=32,
    ) is False


def test_nvfp4_scaled_mm_fast_path_rejects_fp32_input():
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.float32,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    ) is False


def test_nvfp4_scaled_mm_fast_path_requires_in_features_multiple_of_32():
    # Packed K must be %16 per _check_scaled_mm_sizes_v2 -> unpacked K %32.
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=48, out_features=32,
    ) is False
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=32, out_features=32,
    ) is True


def test_nvfp4_scaled_mm_fast_path_requires_out_features_multiple_of_16():
    assert _nvfp4_scaled_mm_fast_path_ok(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=24,
    ) is False


# --- nvfp4 dynamic activation quantisation -----------------------


def test_quantize_nvfp4_dynamic_matches_weight_quantizer_nibble_convention():
    # Bit-exact agreement (not just close values): the activation quantiser
    # must pack codes with the SAME nibble order / e2m1 grid as the
    # established weight-side reference, given the same derived tensor_scale.
    torch.manual_seed(30)
    m, k = 8, 64
    x = torch.randn(m, k) * 0.3
    packed, block_sw, tensor_scale = _quantize_nvfp4_dynamic(x)

    ref_packed, ref_block_sw, _, _ = _ref_quantize_nvfp4(x, tensor_scale)
    assert torch.equal(packed, ref_packed)
    assert torch.equal(block_sw, ref_block_sw)


def test_quantize_nvfp4_dynamic_output_dtypes_and_scale_shape():
    x = torch.randn(16, 64) * 0.2
    packed, block_sw, tensor_scale = _quantize_nvfp4_dynamic(x)
    assert packed.dtype == torch.uint8 and packed.shape == (16, 32)
    assert block_sw.dtype == torch.float8_e4m3fn
    assert tensor_scale.dtype == torch.float32 and tensor_scale.ndim == 0


@pytest.mark.parametrize("m,k", [(200, 64), (17, 32), (129, 512)])
def test_quantize_nvfp4_dynamic_blocked_scale_matches_scaled_mm_v2_size_contract(m, k):
    # expected_scale_elems = round_up(M, 128) * round_up(ceil_div(K, 16), 4) --
    # torch/_meta_registrations.py's _check_scaled_mm_sizes_v2 "is_nv" branch.
    x = torch.randn(m, k) * 0.1
    packed, block_sw, _ = _quantize_nvfp4_dynamic(x)
    num_blocks = -(-k // 16)
    nrb = -(-m // 128)
    ncb = -(-num_blocks // 4)
    assert block_sw.numel() == nrb * 128 * ncb * 4


def test_quantize_nvfp4_dynamic_roundtrip_within_grid_tolerance():
    # allclose, not torch.equal: this checks lossy-representation fidelity
    # (the inherent e2m1/e4m3 grid error), not bit-for-bit algorithm
    # agreement (that's the nibble-convention test above).
    torch.manual_seed(31)
    m, k = 16, 128
    x = torch.randn(m, k) * 0.05
    packed, block_sw, tensor_scale = _quantize_nvfp4_dynamic(x)
    deq = dequantize_nvfp4(packed, block_sw, tensor_scale, m, k)
    rel = (deq - x).abs().mean() / x.abs().mean()
    assert 0.0 <= rel < 0.25  # 4-bit grid error, not corruption


def test_quantize_nvfp4_dynamic_handles_all_zero_block_without_nan():
    # A padded/masked activation slice can be exactly zero -- routine at
    # runtime, unlike a trained weight block. The reference algorithm's
    # block-scale division would be 0/0 = NaN for such a block.
    x = torch.zeros(16, 32)
    x[0, :16] = torch.randn(16) * 0.1  # one nonzero block elsewhere, tensor_scale > 0
    packed, block_sw, tensor_scale = _quantize_nvfp4_dynamic(x)
    deq = dequantize_nvfp4(packed, block_sw, tensor_scale, 16, 32)
    assert torch.isfinite(deq).all()
    assert torch.count_nonzero(deq[0, 16:]) == 0
    assert torch.count_nonzero(deq[1:, :]) == 0


# --- nvfp4 native GEMM forward ------------------------------------


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


def test_nvfp4_load_keeps_raw_scales_alongside_precomputed_dequant_scale():
    # The raw swizzled block scale + global scale must be kept
    # ALONGSIDE the precomputed natural nvfp4_scale, not instead of it --
    # the GEMM fast path needs the raw form, the dequant path needs the
    # precomputed one.
    lin = _nvfp4_layer()
    assert lin.nvfp4_scale is not None
    assert lin.nvfp4_weight_block_scale is not None
    assert lin.nvfp4_weight_block_scale.dtype == torch.float8_e4m3fn
    assert lin.nvfp4_weight_global_scale is not None
    assert lin.nvfp4_weight_global_scale.dtype == torch.float32
    assert lin.nvfp4_weight_global_scale.numel() == 1


def test_forward_nvfp4_scaled_mm_calls_f_scaled_mm_with_expected_layout_and_dtypes():
    # Exercises Nvfp4Linear._forward_nvfp4_scaled_mm directly (bypassing the
    # $NATIVE_NVFP4_MATMUL / hardware-probe gate, which forward_comfy_cast_weights
    # already checked) — asserts the exact args: fp4 dtype on both operands,
    # [block, global] scale pairs with the recipe torch's nvfp4 kernel expects,
    # SWIZZLE_32_4_4 on both block scales, out_dtype == input.dtype, and the
    # weight operand laid out COLUMN-major.
    lin = _nvfp4_layer(in_f=32, out_f=16, bias=True)
    x = torch.randn(2, 3, 32, dtype=torch.bfloat16)
    fake_out = torch.zeros(6, 16, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", return_value=fake_out) as mock_scaled_mm:
        out = lin._forward_nvfp4_scaled_mm(x)

    assert out.shape == (2, 3, 16)
    mock_scaled_mm.assert_called_once()
    args, kwargs = mock_scaled_mm.call_args
    mat_a, mat_b, scale_a, recipe_a, scale_b, recipe_b = args[:6]

    assert mat_a.dtype == torch.float4_e2m1fn_x2 and mat_a.shape == (6, 16)
    assert mat_b.dtype == torch.float4_e2m1fn_x2 and mat_b.shape == (16, 16)
    # column-major: b's transpose (N, K_packed) is the contiguous tensor.
    assert mat_b.t().is_contiguous()

    assert scale_a[0].dtype == torch.float8_e4m3fn and scale_a[1].dtype == torch.float32
    assert scale_b[0].dtype == torch.float8_e4m3fn and scale_b[1].dtype == torch.float32
    assert recipe_a == [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise]
    assert recipe_b == [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise]
    assert kwargs["swizzle_a"] == [F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE]
    assert kwargs["swizzle_b"] == [F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE]
    assert kwargs["output_dtype"] == torch.bfloat16


def test_forward_nvfp4_scaled_mm_adds_bias_on_host_after_gemm():
    # bias is added AFTER the (mocked) GEMM, not passed into the kernel call
    # (see _forward_nvfp4_scaled_mm's docstring for why).
    lin = _nvfp4_layer(in_f=32, out_f=16, bias=True)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    fake_out = torch.ones(1, 16, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", return_value=fake_out) as mock_scaled_mm:
        out = lin._forward_nvfp4_scaled_mm(x)
    _, kwargs = mock_scaled_mm.call_args
    assert "bias" not in kwargs or kwargs["bias"] is None
    assert torch.allclose(out, fake_out + lin.bias.to(torch.bfloat16))


def test_forward_nvfp4_scaled_mm_bails_on_kernel_runtime_error():
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=RuntimeError("no compatible kernel image")):
        assert lin._forward_nvfp4_scaled_mm(x) is None


def test_forward_nvfp4_scaled_mm_bails_on_missing_weight_scales():
    lin = fp8_ops.Linear(32, 16, bias=False)  # never loaded as nvfp4
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")):
        assert lin._forward_nvfp4_scaled_mm(x) is None


def test_forward_nvfp4_scaled_mm_bails_on_mixed_devices():
    # E4-equivalent: a streamed layer's weight scale living on a different
    # device than the packed weight (or the activation) must not reach the
    # kernel. Uses the "meta" device to get a real, distinct torch.device
    # without needing actual CUDA hardware.
    lin = _nvfp4_layer()
    lin.nvfp4_weight_block_scale = lin.nvfp4_weight_block_scale.to("meta")

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")):
        assert lin._forward_nvfp4_scaled_mm(x) is None


def test_forward_falls_back_to_dequant_when_nvfp4_fast_path_bails():
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    expected = lin(x)  # dequant path (fast path not enabled by default)

    # See the fp8 sibling above for why this patches the reason function rather
    # than the `_ok` predicate the call site no longer reads.
    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(wo, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch.object(lin, "_forward_nvfp4_scaled_mm", return_value=None) as attempted:
        out = lin.forward_comfy_cast_weights(x)
    attempted.assert_called_once()
    assert torch.equal(out, expected)


# --- nvfp4 fast-path rejection observability --------------------------------


@pytest.fixture(autouse=True)
def _reset_nvfp4_fast_path_rejection_log():
    reset_nvfp4_fast_path_rejection_log()
    yield
    reset_nvfp4_fast_path_rejection_log()


def test_nvfp4_fast_path_reject_reason_agrees_with_ok_predicate():
    ok_kwargs = dict(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    )
    assert _nvfp4_fast_path_reject_reason(**ok_kwargs) is None
    assert _nvfp4_scaled_mm_fast_path_ok(**ok_kwargs) is True

    bad_kwargs = dict(ok_kwargs, input_dtype=torch.float32)
    reason = _nvfp4_fast_path_reject_reason(**bad_kwargs)
    assert reason is not None and "float32" in reason
    assert _nvfp4_scaled_mm_fast_path_ok(**bad_kwargs) is False


def test_nvfp4_fast_path_reject_reason_distinguishes_weight_device_from_input_dtype():
    # Under partial residency a streamed quantised leaf can still have its
    # weight on pinned CPU RAM at forward entry (weight_is_cuda False) even
    # though the activation is already the right dtype -- a completely
    # different fix (stage the prefetch earlier / $NATIVE_STREAM_PREFETCH)
    # than an input_dtype rejection ($NATIVE_QWEN3_TE_BF16). The two must
    # never collapse into the same reason string.
    weight_not_cuda = _nvfp4_fast_path_reject_reason(
        lora_deltas=None, input_dtype=torch.bfloat16,
        input_is_cuda=True, weight_is_cuda=False, in_features=64, out_features=32,
    )
    wrong_dtype = _nvfp4_fast_path_reject_reason(
        lora_deltas=None, input_dtype=torch.float32,
        input_is_cuda=True, weight_is_cuda=True, in_features=64, out_features=32,
    )
    assert weight_not_cuda == "weight_not_cuda"
    assert wrong_dtype == "input_dtype=torch.float32"
    assert weight_not_cuda != wrong_dtype


def test_forward_comfy_cast_weights_logs_nvfp4_fast_path_rejection_reason(monkeypatch, caplog):
    # This used to be completely silent: gate on + predicate False fell
    # straight through to dequant with zero indication of why -- which is
    # exactly how every Linear silently taking the LUT dequant path went
    # unnoticed. Must fire at least once, and must say WHY.
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    x_wrong_dtype = torch.randn(1, 32, dtype=torch.float32)  # rejects on input_dtype

    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("float32" in w for w in warnings)


def test_forward_comfy_cast_weights_logs_nvfp4_fast_path_rejection_only_once_per_reason(monkeypatch, caplog):
    # Hard requirement: NOT per-Linear-per-forward (~350 linears x N steps
    # would flood the log) -- one line per distinct reason for the process.
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    x_wrong_dtype = torch.randn(1, 32, dtype=torch.float32)

    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        for _ in range(5):
            lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_forward_comfy_cast_weights_does_not_log_when_fast_path_eligible(monkeypatch, caplog):
    # Same CPU-box-can't-be-CUDA-eligible workaround as
    # test_forward_falls_back_to_dequant_when_nvfp4_fast_path_bails: force
    # eligibility past the predicate so this exercises the "took the fast
    # path, no rejection to log" branch instead of the always-false-on-CPU
    # real device check.
    import vendor.gpl.comfyui.ops as wo

    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    fake_out = torch.zeros(1, 16, dtype=torch.bfloat16)

    with patch.object(wo, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(wo, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch("torch.nn.functional.scaled_mm", return_value=fake_out), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


# --- real kernel probe (Blackwell hardware only) ---------------------------
#
# This is the ONLY test in this module that touches a real CUDA kernel. It
# mirrors test_attention.py::test_cross_backend_agreement_if_installed's
# runtime pytest.skip() idiom (skip when CUDA/the right SM isn't present)
# rather than a decorator, since the skip reason needs the live capability
# check. It is NOT executed as part of this ticket's own verification run —
# CUDA is available in this container but the GPU is shared with a live
# maintainer generation, so no CUDA kernel is invoked here by the author of
# this test; it is meant to be run on the actual Blackwell box.


def test_nvfp4_trial_gemm_ok_on_real_hardware():
    if not torch.cuda.is_available():
        pytest.skip("no CUDA device available")
    if torch.cuda.get_device_capability() < (12, 0):
        pytest.skip("nvfp4 x nvfp4 tensor cores need sm120+ (Blackwell)")

    import vendor.gpl.comfyui.ops as wo
    assert wo._nvfp4_trial_gemm_ok() is True


# --- output-side LoRA branch (shared by the fp8 and nvfp4 fast paths) -------
#
# _deltas_output_branch_ok / _lora_output_branch are pure CPU tensor algebra --
# no fp8/nvfp4/CUDA involved -- so the decisive property (the branch's output
# matches the weight-side apply_lora_deltas ground truth) is testable directly,
# without a real _scaled_mm kernel.


def test_deltas_output_branch_ok_accepts_full_width_and_dim0_slice():
    import vendor.gpl.comfyui.ops as wo
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    full = LoraDelta(down=torch.randn(4, 32), up=torch.randn(16, 4), alpha=4.0, scale=1.0)
    sliced = LoraDelta(
        down=torch.randn(3, 32), up=torch.randn(6, 3), alpha=2.0, scale=1.0,
        target_slice=(0, 5, 6),
    )
    assert wo._deltas_output_branch_ok([full], out_features=16) is True
    assert wo._deltas_output_branch_ok([sliced], out_features=16) is True
    assert wo._deltas_output_branch_ok([full, sliced], out_features=16) is True


def test_deltas_output_branch_ok_rejects_lokr():
    import vendor.gpl.comfyui.ops as wo
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    lokr = LoraDelta(down=torch.randn(4, 4), up=torch.randn(4, 4), alpha=4.0, scale=1.0, kron=True)
    assert wo._deltas_output_branch_ok([lokr], out_features=16) is False


def test_deltas_output_branch_ok_rejects_out_of_bounds_slice():
    import vendor.gpl.comfyui.ops as wo
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    # start(12) + length(6) = 18 > out_features(16).
    oob = LoraDelta(
        down=torch.randn(3, 32), up=torch.randn(6, 3), alpha=2.0, scale=1.0,
        target_slice=(0, 12, 6),
    )
    assert wo._deltas_output_branch_ok([oob], out_features=16) is False


def test_scaled_mm_fast_path_reason_none_for_branch_ok_full_and_sliced_deltas():
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    kwargs = dict(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True,
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    )
    full = LoraDelta(down=torch.randn(4, 32), up=torch.randn(32, 4), alpha=4.0, scale=1.0)
    sliced = LoraDelta(
        down=torch.randn(3, 32), up=torch.randn(6, 3), alpha=2.0, scale=1.0,
        target_slice=(0, 5, 6),
    )
    assert _scaled_mm_fast_path_reject_reason(lora_deltas=[full], **kwargs) is None
    assert _scaled_mm_fast_path_reject_reason(lora_deltas=[sliced], **kwargs) is None


def test_scaled_mm_fast_path_reason_still_lora_deltas_for_lokr():
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    kwargs = dict(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True,
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    )
    lokr = LoraDelta(down=torch.randn(4, 4), up=torch.randn(4, 4), alpha=4.0, scale=1.0, kron=True)
    assert _scaled_mm_fast_path_reject_reason(lora_deltas=[lokr], **kwargs) == "lora_deltas"


def test_nvfp4_fast_path_reason_none_for_branch_ok_sliced_delta():
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    kwargs = dict(
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    )
    sliced = LoraDelta(
        down=torch.randn(3, 32), up=torch.randn(6, 3), alpha=2.0, scale=1.0,
        target_slice=(0, 5, 6),
    )
    assert _nvfp4_fast_path_reject_reason(lora_deltas=[sliced], **kwargs) is None


def test_nvfp4_fast_path_reason_still_lora_deltas_for_lokr():
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    kwargs = dict(
        input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
        in_features=32, out_features=32,
    )
    lokr = LoraDelta(down=torch.randn(4, 4), up=torch.randn(4, 4), alpha=4.0, scale=1.0, kron=True)
    assert _nvfp4_fast_path_reject_reason(lora_deltas=[lokr], **kwargs) == "lora_deltas"


def test_forward_scaled_mm_lora_output_branch_matches_dequant_parity():
    # The decisive property: for a mix of a full-width rank-4 delta and a
    # dim-0 target_slice delta (the shape a fused qkv Linear's per-projection
    # LoRA deltas land as, per apply.py's map_lora_keys), the output-side
    # branch must equal what apply_lora_deltas produces on the weight side --
    # x @ (weight + deltas).T == x @ weight.T + branch(x, deltas).
    import vendor.gpl.comfyui.ops as wo
    from src.platform.runtime.native.lora.key_mapping import LoraDelta

    torch.manual_seed(0)
    out_features, in_features, m = 16, 32, 5
    weight = torch.randn(out_features, in_features, dtype=torch.float32)
    x = torch.randn(m, in_features, dtype=torch.float32)

    full = LoraDelta(down=torch.randn(4, in_features), up=torch.randn(out_features, 4), alpha=4.0, scale=1.3)
    sliced = LoraDelta(
        down=torch.randn(3, in_features), up=torch.randn(6, 3), alpha=2.0, scale=0.7,
        target_slice=(0, 5, 6),
    )
    deltas = [full, sliced]
    assert wo._deltas_output_branch_ok(deltas, out_features=out_features) is True

    expected_weight = wo.apply_lora_deltas(weight.clone(), deltas)
    expected = x @ expected_weight.T

    branch = wo._lora_output_branch(x, deltas, torch.float32, out_features)
    actual = x @ weight.T + branch

    torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-4)
