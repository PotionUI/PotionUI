"""Fp8ScaledLinear._forward_scaled_mm's per-layer scale-validity lifecycle.

The old implementation re-validated weight_scale/input_scale (numel, then a
device->host isfinite + float() read pair, each) on EVERY forward, even though
the scale tensors never change between forwards in the common case. These
tests prove the cache (`_cached_scaled_mm_scale` / `_scale_identity_key`)
collapses that to one validation per distinct scale identity, while every
observable behaviour (return value, fallback decisions, numerics) stays
exactly what the old, uncached implementation produced -- checked here by
literally keeping the old body (`_reference_forward_scaled_mm`) and running
both against the same faked `torch._scaled_mm`.

CPU-only: `torch._scaled_mm` is a real CUDA kernel, so every test here
monkeypatches it with a fake that both implementations call identically.
"""

from __future__ import annotations

import numpy._core.multiarray  # noqa: F401

from unittest.mock import patch

import pytest
import torch

from vendor.gpl.comfyui.ops import (
    _E4M3_MAX,
    _quantize_fp8_dynamic,
    fp8_ops,
)


def _fp8_layer(w_scale, *, input_scale=None, bias=False, in_f=16, out_f=16, weight=None):
    has_bias = isinstance(bias, torch.Tensor) or bool(bias)
    lin = fp8_ops.Linear(in_f, out_f, bias=has_bias)
    if weight is None:
        weight = torch.zeros(out_f, in_f, dtype=torch.float8_e4m3fn)
    sd = {"weight": weight, "weight_scale": w_scale}
    if input_scale is not None:
        sd["input_scale"] = input_scale
    if isinstance(bias, torch.Tensor):
        sd["bias"] = bias
    elif bias:
        sd["bias"] = torch.zeros(out_f)
    lin.load_state_dict(sd, strict=False, assign=True)
    return lin


def _reference_forward_scaled_mm(self, input: torch.Tensor) -> "torch.Tensor | None":
    """Verbatim copy of the pre-change `_forward_scaled_mm` body (the
    uncached implementation): re-validates both scales from scratch on every
    call. Kept here, not in the source, purely as this test's ground truth."""
    device = input.device
    w_scale_t, x_scale_t = self.weight_scale, self.input_scale
    if w_scale_t.numel() != 1 or (x_scale_t is not None and x_scale_t.numel() != 1):
        return None
    w_scale = w_scale_t.to(device=device, dtype=torch.float32).reshape(())
    if not bool(torch.isfinite(w_scale)) or float(w_scale) <= 0.0:
        return None

    orig_shape = input.shape
    x2d = input.reshape(-1, orig_shape[-1]).contiguous()
    if x_scale_t is not None:
        x_scale = x_scale_t.to(device=device, dtype=torch.float32).reshape(())
        if not bool(torch.isfinite(x_scale)) or float(x_scale) <= 0.0:
            return None
        x_fp8 = (x2d.to(torch.float32) / x_scale).clamp(-_E4M3_MAX, _E4M3_MAX).to(torch.float8_e4m3fn)
    else:
        x_fp8, x_scale = _quantize_fp8_dynamic(x2d)
    weight = self.weight if self.weight.is_contiguous() else self.weight.contiguous()
    bias = self.bias.to(device=device, dtype=input.dtype) if self.bias is not None else None
    try:
        out = torch._scaled_mm(
            x_fp8, weight.t(),
            scale_a=x_scale, scale_b=w_scale,
            out_dtype=input.dtype, bias=bias,
        )
    except (RuntimeError, torch.cuda.OutOfMemoryError):
        return None
    if self.lora_deltas:
        raise AssertionError("not exercised by these fixtures")
    return out.reshape(*orig_shape[:-1], -1)


@pytest.fixture
def counting_isfinite(monkeypatch):
    """Counts device->host `torch.isfinite` reads -- the seam both the old
    inline check and the new `_read_finite_positive_scalar` helper go
    through -- without changing its behaviour."""
    calls = {"n": 0}
    real_isfinite = torch.isfinite

    def _wrapped(t):
        calls["n"] += 1
        return real_isfinite(t)

    monkeypatch.setattr(torch, "isfinite", _wrapped)
    return calls


def _fake_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
    """Cheap fake for tests that only assert on call counts / fallback
    decisions / the exact scale tensors passed in -- never on the numeric
    output, so returning zeros regardless of the operands is fine."""
    return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype, device=a.device)


def _fake_scaled_mm_math(a, b, *, scale_a, scale_b, out_dtype, bias=None):
    """CPU stand-in for ``torch._scaled_mm`` that computes the ACTUAL scaled
    matmul (dequantize both operands, matmul in fp32, add bias, cast) instead
    of returning zeros. A fake that ignores its operands can't tell a test
    "the cache fed the right scale into the kernel" apart from "the cache fed
    anything at all" -- this one can, since a wrong or stale scale changes the
    result."""
    out = (a.to(torch.float32) * scale_a) @ (b.to(torch.float32) * scale_b)
    if bias is not None:
        out = out + bias.to(torch.float32)
    return out.to(out_dtype)


# --- call-count: unchanged scale validated once across N forwards ----------


def test_unchanged_weight_only_scale_validated_once_across_n_forwards(counting_isfinite):
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(5):
            out = lin._forward_scaled_mm(x)
    assert out is not None
    # old code: 1 isfinite call per forward x 5 forwards = 5. new: validated once.
    assert counting_isfinite["n"] == 1


def test_unchanged_weight_and_input_scale_validated_once_across_n_forwards(counting_isfinite):
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02))
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(5):
            out = lin._forward_scaled_mm(x)
    assert out is not None
    # old code: 2 isfinite calls (weight + input) per forward x 5 = 10. new: 1 pair = 2.
    assert counting_isfinite["n"] == 2


def test_reference_uncached_implementation_reads_every_forward(counting_isfinite):
    # Sanity check for the counting fixture itself: the OLD body really does
    # re-read on every call (proves the "before" side of the call-count table).
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02))
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(5):
            _reference_forward_scaled_mm(lin, x)
    assert counting_isfinite["n"] == 10


# --- invalidation: replaced / mutated / moved scale is rechecked -----------


def test_replaced_scale_tensor_is_rechecked(counting_isfinite):
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 1
        lin.weight_scale = torch.tensor(0.02)  # attribute reassignment, new tensor object
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 2


def test_inplace_mutated_scale_is_rechecked(counting_isfinite):
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 1
        lin.weight_scale.fill_(0.03)  # same object, bumps _version
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 2


def test_reloaded_state_dict_scale_is_rechecked(counting_isfinite):
    # _load_from_state_dict reassigns self.weight_scale to a new tensor.
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 1
        lin.load_state_dict(
            {"weight": lin.weight.data, "weight_scale": torch.tensor(0.05)}, strict=False, assign=True,
        )
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 2


def test_module_moved_via_to_is_rechecked_once_then_cached_again(counting_isfinite):
    # .to() on a real (CPU) device is a no-op copy for an already-CPU buffer
    # UNLESS the dtype actually changes, so force a visible move by casting to
    # a different dtype-carrying .to() call that nn.Module._apply always
    # replaces the buffer for: .to(dtype=torch.float64) then back.
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 1
        original_id = id(lin.weight_scale)
        lin.to(torch.float64)
        assert id(lin.weight_scale) != original_id  # _apply replaced the buffer
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 2
        # unchanged again after the move -> no further reads.
        lin._forward_scaled_mm(x)
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 2


def test_scale_created_under_inference_mode_is_never_cached_across_forwards(counting_isfinite):
    # A tensor minted inside torch.inference_mode() is an "inference tensor"
    # that never tracks a version counter -- reading `._version` on it raises
    # RuntimeError regardless of the CALLER's mode. The cache must fall back
    # to "always revalidate" for it instead of crashing or (wrongly) treating
    # it as immutable forever.
    with torch.inference_mode():
        w_scale = torch.tensor(0.01)
    assert w_scale.is_inference()
    lin = _fp8_layer(w_scale)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(3):
            out = lin._forward_scaled_mm(x)
    assert out is not None
    assert counting_isfinite["n"] == 3  # revalidated every forward, no crash


def test_scale_replaced_under_inference_mode_is_rechecked_without_crash(counting_isfinite):
    lin = _fp8_layer(torch.tensor(0.01))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 1

        with torch.inference_mode():
            lin.weight_scale = torch.tensor(0.02)
        assert lin.weight_scale.is_inference()

        out = lin._forward_scaled_mm(x)  # must not raise reading ._version
        assert out is not None
        assert counting_isfinite["n"] == 2
        lin._forward_scaled_mm(x)
        assert counting_isfinite["n"] == 3  # still uncacheable on a repeat call


def test_forward_under_inference_mode_with_ordinary_scale_still_caches(counting_isfinite):
    # The scale tensor was created OUTSIDE inference_mode (ordinary,
    # version-tracked); calling _forward_scaled_mm from WITHIN an
    # inference_mode region must not itself disable caching -- only a scale
    # tensor that is itself an inference tensor should.
    lin = _fp8_layer(torch.tensor(0.01))
    assert not lin.weight_scale.is_inference()
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    with torch.inference_mode(), patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(4):
            out = lin._forward_scaled_mm(x)
    assert out is not None
    assert counting_isfinite["n"] == 1


# --- fallback parity: nonfinite / zero / negative / non-scalar -------------


@pytest.mark.parametrize(
    "bad_weight_scale",
    [torch.tensor(0.0), torch.tensor(float("inf")), torch.tensor(float("-inf")),
     torch.tensor(float("nan")), torch.tensor(-0.5), torch.full((16, 1), 0.01)],
)
def test_bad_weight_scale_falls_back_first_and_repeated_call(bad_weight_scale):
    lin = _fp8_layer(bad_weight_scale)
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
        assert lin._forward_scaled_mm(x) is None
        assert lin._forward_scaled_mm(x) is None  # repeated call: still falls back, no crash


@pytest.mark.parametrize(
    "bad_input_scale",
    [torch.tensor(0.0), torch.tensor(float("inf")), torch.tensor(float("nan")),
     torch.tensor(-1.0), torch.tensor([0.02, 0.03])],
)
def test_bad_input_scale_falls_back_first_and_repeated_call(bad_input_scale):
    lin = _fp8_layer(torch.tensor(0.01), input_scale=bad_input_scale)
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", side_effect=AssertionError("must not be called")):
        assert lin._forward_scaled_mm(x) is None
        assert lin._forward_scaled_mm(x) is None


def test_dynamic_activation_path_unaffected_when_input_scale_none():
    lin = _fp8_layer(torch.tensor(0.01))
    assert lin.input_scale is None
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    seen = {}

    def _fake(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        seen["scale_a"] = scale_a
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype, device=a.device)

    with patch("torch._scaled_mm", _fake):
        out = lin._forward_scaled_mm(x)
    assert out is not None
    x2d = x.reshape(-1, 16)
    _, expected_dynamic_scale = _quantize_fp8_dynamic(x2d)
    torch.testing.assert_close(seen["scale_a"], expected_dynamic_scale)


def test_static_input_scale_value_matches_reference():
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02))
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    seen = {}

    def _fake(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        seen["scale_a"] = scale_a
        seen["scale_b"] = scale_b
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype, device=a.device)

    with patch("torch._scaled_mm", _fake):
        lin._forward_scaled_mm(x)
    assert torch.equal(seen["scale_a"], torch.tensor(0.02))
    assert torch.equal(seen["scale_b"], torch.tensor(0.01))


# --- output parity: new (cached) matches old (uncached) implementation -----


@pytest.mark.parametrize("with_input_scale", [False, True])
def test_output_matches_reference_across_repeated_forwards(with_input_scale):
    torch.manual_seed(0)
    real_w = torch.randn(16, 16) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    sd = {"weight": w_fp8, "weight_scale": w_scale, "bias": torch.randn(16)}
    if with_input_scale:
        sd["input_scale"] = torch.tensor(0.02)
    new_lin = fp8_ops.Linear(16, 16, bias=True)
    new_lin.load_state_dict(sd, strict=False, assign=True)
    ref_lin = fp8_ops.Linear(16, 16, bias=True)
    ref_lin.load_state_dict(dict(sd), strict=False, assign=True)

    with patch("torch._scaled_mm", _fake_scaled_mm_math):
        for _ in range(3):
            x = torch.randn(2, 16, dtype=torch.bfloat16)
            new_out = new_lin._forward_scaled_mm(x)
            ref_out = _reference_forward_scaled_mm(ref_lin, x)
            torch.testing.assert_close(new_out, ref_out)


def test_changed_valid_scale_changes_output_correctly():
    # Same fp8 weight codes, two different (both valid) weight_scale values:
    # proves the cache doesn't keep serving the first scale into the matmul
    # after a reassignment -- with the zero-returning fake this couldn't be
    # told apart from a stale cache, so this uses the real-math fake.
    torch.manual_seed(1)
    real_w = torch.randn(16, 16) * 0.05
    calib_scale = torch.tensor(0.01)
    w_fp8 = (real_w / calib_scale).clamp(-_E4M3_MAX, _E4M3_MAX).to(torch.float8_e4m3fn)
    bias = torch.randn(16)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    lin = _fp8_layer(torch.tensor(0.01), weight=w_fp8, bias=bias)

    def _expected(w_scale_value: float) -> torch.Tensor:
        # matches _forward_scaled_mm's own layout: torch._scaled_mm(x_fp8,
        # weight.t(), ...) -- weight.t() is the (in, out) operand, not weight
        # itself.
        w_scale = torch.tensor(w_scale_value)
        x2d = x.reshape(-1, 16)
        x_fp8, x_scale = _quantize_fp8_dynamic(x2d)
        out = (x_fp8.to(torch.float32) * x_scale) @ (w_fp8.t().to(torch.float32) * w_scale)
        # _forward_scaled_mm casts bias to input.dtype (bf16) BEFORE handing it
        # to the kernel -- match that rounding, not the original fp32 bias.
        bias_bf16 = bias.to(x.dtype)
        return (out + bias_bf16.to(torch.float32)).to(x.dtype)

    with patch("torch._scaled_mm", _fake_scaled_mm_math):
        out1 = lin._forward_scaled_mm(x)
        torch.testing.assert_close(out1, _expected(0.01))

        lin.weight_scale = torch.tensor(0.02)  # reassignment -> must be rechecked AND actually used
        out2 = lin._forward_scaled_mm(x)

    torch.testing.assert_close(out2, _expected(0.02))
    assert not torch.allclose(out1.float(), out2.float())


def test_metadata_size_per_layer_is_two_small_entries():
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
    # one (key, cast-scalar-tensor) entry per scale slot -- weight + input.
    assert set(lin._scale_preflight.keys()) == {"weight", "input"}
    assert len(lin._scale_preflight) == 2
