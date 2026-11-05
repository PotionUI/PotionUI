from __future__ import annotations

from unittest.mock import patch
import pytest
import torch
import torch.nn.functional as F
from tests.fixtures.kernel_gemm import installed_gemm_verdicts
from src.platform.runtime.native.io.quant_formats import quantize_nvfp4_dynamic
from src.platform.runtime.native.kernels import fp8_ops, nvfp4_linear
from src.platform.runtime.native.kernels.fp8_linear import _quantize_fp8_dynamic
from src.platform.runtime.native.kernels.nvfp4_linear import (
    _nvfp4_fast_path_reject_reason,
    _nvfp4_matmul_enabled,
    _nvfp4_scaled_mm_fast_path_ok,
    reset_nvfp4_fast_path_rejection_log,
)
from .._nvfp4_ref import F4_MAX as _F4_MAX_REF
from .._nvfp4_ref import F8_MAX as _F8_MAX_REF
from .._nvfp4_ref import quantize_nvfp4 as _ref_quantize_nvfp4
from src.platform.settings.runtime_flags import runtime_flag_values


def test_nvfp4_matmul_disabled_by_default(monkeypatch):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", False)
    with installed_gemm_verdicts(nvfp4=True):
        assert _nvfp4_matmul_enabled() is False


def test_nvfp4_matmul_flag_off_never_uses_fast_path_even_if_probe_true(monkeypatch):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", False)
    with installed_gemm_verdicts(nvfp4=True):
        assert _nvfp4_matmul_enabled() is False


def test_nvfp4_matmul_flag_on_requires_probe(monkeypatch):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", True)
    with installed_gemm_verdicts(nvfp4=False):
        assert _nvfp4_matmul_enabled() is False
    with installed_gemm_verdicts(nvfp4=True):
        assert _nvfp4_matmul_enabled() is True


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


@pytest.mark.parametrize("quantize", [_quantize_fp8_dynamic, quantize_nvfp4_dynamic], ids=["fp8", "nvfp4"])
def test_activation_quantisers_leave_an_fp32_input_untouched(quantize):
    x = torch.randn(8, 64) * 0.3
    before = x.clone()
    quantize(x)
    assert torch.equal(x, before)


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


def test_nvfp4_load_keeps_raw_scales_and_defers_the_dequant_table():
    lin = _nvfp4_layer()
    assert lin.nvfp4_scale is None
    assert lin.nvfp4_weight_block_scale is not None
    assert lin.nvfp4_weight_block_scale.dtype == torch.float8_e4m3fn
    assert lin.nvfp4_weight_global_scale is not None
    assert lin.nvfp4_weight_global_scale.dtype == torch.float32
    assert lin.nvfp4_weight_global_scale.numel() == 1


def test_forward_nvfp4_scaled_mm_calls_f_scaled_mm_with_expected_layout_and_dtypes():
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
    assert mat_b.t().is_contiguous()

    assert scale_a[0].dtype == torch.float8_e4m3fn and scale_a[1].dtype == torch.float32
    assert scale_b[0].dtype == torch.float8_e4m3fn and scale_b[1].dtype == torch.float32
    assert recipe_a == [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise]
    assert recipe_b == [F.ScalingType.BlockWise1x16, F.ScalingType.TensorWise]
    assert kwargs["swizzle_a"] == [F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE]
    assert kwargs["swizzle_b"] == [F.SwizzleType.SWIZZLE_32_4_4, F.SwizzleType.NO_SWIZZLE]
    assert kwargs["output_dtype"] == torch.bfloat16


def test_forward_nvfp4_scaled_mm_adds_bias_on_host_after_gemm():
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
    lin = fp8_ops.Linear(32, 16, bias=False)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")):
        assert lin._forward_nvfp4_scaled_mm(x) is None


def test_forward_nvfp4_scaled_mm_bails_on_mixed_devices():
    lin = _nvfp4_layer()
    lin.nvfp4_weight_block_scale = lin.nvfp4_weight_block_scale.to("meta")

    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")):
        assert lin._forward_nvfp4_scaled_mm(x) is None


def test_forward_falls_back_to_dequant_when_nvfp4_fast_path_bails():
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    expected = lin(x)

    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(nvfp4_linear, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch.object(lin, "_forward_nvfp4_scaled_mm", return_value=None) as attempted:
        out = lin.forward_comfy_cast_weights(x)
    attempted.assert_called_once()
    assert torch.equal(out, expected)


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
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x_wrong_dtype = torch.randn(1, 32, dtype=torch.float32)

    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("float32" in w for w in warnings)


def test_forward_comfy_cast_weights_logs_nvfp4_fast_path_rejection_only_once_per_reason(monkeypatch, caplog):
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x_wrong_dtype = torch.randn(1, 32, dtype=torch.float32)

    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch("torch.nn.functional.scaled_mm", side_effect=AssertionError("must not run")), \
         caplog.at_level("WARNING"):
        for _ in range(5):
            lin.forward_comfy_cast_weights(x_wrong_dtype)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1


def test_forward_comfy_cast_weights_does_not_log_when_fast_path_eligible(monkeypatch, caplog):
    lin = _nvfp4_layer(in_f=32, out_f=16)
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    fake_out = torch.zeros(1, 16, dtype=torch.bfloat16)

    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(nvfp4_linear, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch("torch.nn.functional.scaled_mm", return_value=fake_out), \
         caplog.at_level("WARNING"):
        lin.forward_comfy_cast_weights(x)

    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_nvfp4_layers_running_with_the_setting_off_warn_once_per_process(monkeypatch, caplog):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", False)
    layers = [_nvfp4_layer(seed=1), _nvfp4_layer(seed=2)]
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with installed_gemm_verdicts(nvfp4=True), caplog.at_level("WARNING"):
        for _ in range(3):
            for lin in layers:
                lin(x)

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "dequantized" in warnings[0] and "'nvfp4 fast multiply' setting" in warnings[0]
    assert "Admin → Backends → Optimizations" in warnings[0]


def test_nvfp4_layers_on_the_fast_path_with_the_setting_on_never_warn(monkeypatch, caplog):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", True)
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with installed_gemm_verdicts(nvfp4=True), \
         patch.object(nvfp4_linear, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch("torch.nn.functional.scaled_mm", return_value=torch.zeros(1, 16, dtype=torch.bfloat16)), \
         caplog.at_level("WARNING"):
        for _ in range(3):
            lin.forward_comfy_cast_weights(x)

    assert _warnings(caplog) == []


def test_the_setting_on_without_fp4_gemm_support_warns_with_the_probe_reason(monkeypatch, caplog):
    monkeypatch.setitem(runtime_flag_values(), "native_nvfp4_matmul", True)
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    with installed_gemm_verdicts(nvfp4=False), caplog.at_level("WARNING"):
        lin(x)
        lin(x)

    warnings = _warnings(caplog)
    assert len(warnings) == 1 and "faked unsupported" in warnings[0]


@pytest.mark.parametrize(("attempt", "reason"), [
    ("activation", "activation quant failed (ValueError)"),
    ("kernel", "scaled_mm rejected operands (RuntimeError)"),
])
def test_a_failed_fast_path_attempt_warns_once_with_its_cause(attempt, reason, caplog):
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    quant = patch.object(nvfp4_linear, "quantize_nvfp4_activation", side_effect=ValueError("bad activations")) \
        if attempt == "activation" else patch("torch.nn.functional.scaled_mm", side_effect=RuntimeError("no kernel image"))
    with quant, caplog.at_level("WARNING"):
        for _ in range(3):
            assert lin._forward_nvfp4_scaled_mm(x) is None

    warnings = _warnings(caplog)
    assert len(warnings) == 1 and reason in warnings[0]
    assert ("bad activations" if attempt == "activation" else "no kernel image") in warnings[0]


def test_missing_or_misplaced_weight_scales_warn_instead_of_bailing_silently(caplog):
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    misplaced = _nvfp4_layer()
    misplaced.nvfp4_weight_block_scale = misplaced.nvfp4_weight_block_scale.to("meta")
    with caplog.at_level("WARNING"):
        assert fp8_ops.Linear(32, 16, bias=False)._forward_nvfp4_scaled_mm(x) is None
        assert misplaced._forward_nvfp4_scaled_mm(x) is None

    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert "weight scales missing" in warnings[0] and "weight scales not on the input device" in warnings[1]


def _fast_path_on(fake_out=None):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True))
    stack.enter_context(patch.object(nvfp4_linear, "_nvfp4_fast_path_reject_reason", return_value=None))
    stack.enter_context(patch("torch.nn.functional.scaled_mm",
                              return_value=torch.zeros(1, 16, dtype=torch.bfloat16) if fake_out is None else fake_out))
    return stack


def test_the_fast_path_never_builds_a_scale_table():
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    assert lin.nvfp4_scale is None
    with _fast_path_on(), patch.object(nvfp4_linear, "precompute_nvfp4_scale",
                                       side_effect=AssertionError("built a table on the fast path")):
        for _ in range(3):
            lin.forward_comfy_cast_weights(x)
    assert lin.nvfp4_scale is None


def test_a_fast_path_success_frees_the_table_an_earlier_dequant_built():
    lin = _nvfp4_layer()
    x = torch.randn(1, 32, dtype=torch.bfloat16)
    lin(x)
    assert lin.nvfp4_scale is not None
    with _fast_path_on():
        lin.forward_comfy_cast_weights(x)
    assert lin.nvfp4_scale is None


def test_the_first_dequant_fallback_builds_the_table_and_keeps_dequant_numerics():
    from .._nvfp4_ref import old_dequantize_nvfp4

    torch.manual_seed(41)
    w = torch.randn(16, 32) * 0.05
    pts = (w.abs().amax() / (_F4_MAX_REF * _F8_MAX_REF)).clamp(min=1e-8).detach()
    packed, block_sw, _, _ = _ref_quantize_nvfp4(w, pts)
    lin = fp8_ops.Linear(32, 16, bias=False)
    lin._load_from_state_dict({"weight": packed, "weight_scale": block_sw, "weight_scale_2": pts.clone()},
                              "", {}, True, [], [], [])
    x = torch.randn(2, 32, dtype=torch.bfloat16)
    expected = F.linear(x, old_dequantize_nvfp4(packed, block_sw, pts, 16, 32).to(torch.bfloat16))

    with patch.object(nvfp4_linear, "_nvfp4_matmul_enabled", return_value=True), \
         patch.object(nvfp4_linear, "_nvfp4_fast_path_reject_reason", return_value=None), \
         patch.object(lin, "_forward_nvfp4_scaled_mm", return_value=None):
        assert lin.nvfp4_scale is None
        out = lin.forward_comfy_cast_weights(x)
        table = lin.nvfp4_scale
        again = lin.forward_comfy_cast_weights(x)

    assert table is not None and table.dtype == torch.float32 and table.shape == (16, 2)
    assert lin.dequant_table_bytes() == table.numel() * table.element_size()
    assert lin.nvfp4_scale is table
    assert torch.equal(out, expected) and torch.equal(again, expected)
