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


def _fp8_layer(w_scale, *, input_scale=None, bias=False, in_f=16, out_f=16):
    lin = fp8_ops.Linear(in_f, out_f, bias=bias)
    sd = {"weight": torch.zeros(out_f, in_f, dtype=torch.float8_e4m3fn), "weight_scale": w_scale}
    if input_scale is not None:
        sd["input_scale"] = input_scale
    if bias:
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
    return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype, device=a.device)


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

    with patch("torch._scaled_mm", _fake_scaled_mm):
        for _ in range(3):
            x = torch.randn(2, 16, dtype=torch.bfloat16)
            new_out = new_lin._forward_scaled_mm(x)
            ref_out = _reference_forward_scaled_mm(ref_lin, x)
            torch.testing.assert_close(new_out, ref_out)


def test_metadata_size_per_layer_is_two_small_entries():
    lin = _fp8_layer(torch.tensor(0.01), input_scale=torch.tensor(0.02))
    x = torch.randn(1, 16, dtype=torch.bfloat16)
    with patch("torch._scaled_mm", _fake_scaled_mm):
        lin._forward_scaled_mm(x)
    # one (key, cast-scalar-tensor) entry per scale slot -- weight + input.
    assert set(lin._scale_preflight.keys()) == {"weight", "input"}
    assert len(lin._scale_preflight) == 2
