from __future__ import annotations

from collections import Counter

import pytest
import torch
from torch.profiler import ProfilerActivity, profile

from src.platform.runtime.native.kernels import (
    act_gate,
    capabilities,
    fused_elementwise,
    gated_residual,
    norm_modulate,
)
from src.platform.settings.runtime_flags import runtime_flag_values

pytestmark = pytest.mark.requires_gpu

DTYPES = [torch.bfloat16, torch.float16]
WIDTHS = [3072, 3840, 5120, 5376]
FP32_TERM = 2.0 ** -20


@pytest.fixture(autouse=True)
def _fused_on(monkeypatch):
    caps = capabilities()
    if not torch.cuda.is_available() or not caps.triton or not caps.device(0).at_least(8, 0):
        pytest.skip("needs a CUDA GPU of compute capability 8.0+ with Triton")
    fused_elementwise.reset_fused_elementwise_state()
    monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", True)
    routed = []
    real = fused_elementwise._run_fused

    def counting(name, *args):
        routed.append(name)
        return real(name, *args)

    monkeypatch.setattr(fused_elementwise, "_run_fused", counting)
    yield
    assert fused_elementwise.fused_elementwise_disabled_reason() is None
    assert routed, "no call took the Triton path"


def _t(*shape, dtype=torch.float32, seed=0, scale=1.0):
    g = torch.Generator(device="cuda").manual_seed(seed)
    return (torch.randn(*shape, device="cuda", generator=g) * scale).to(dtype)


def _assert_one_rounding(out: torch.Tensor, ref: torch.Tensor, magnitude: torch.Tensor, dtype: torch.dtype) -> None:
    assert out.dtype == dtype, (out.dtype, dtype)
    error = (out.double() - ref).abs()
    allowed = torch.finfo(dtype).eps * ref.abs() + FP32_TERM * magnitude + torch.finfo(dtype).tiny
    assert bool((error <= allowed).all()), float((error - allowed).max())


def _norm_ref(x, norm, eps, weight=None, weight_offset=0.0, weight_round=False):
    xd = x.double()
    if norm == "layer":
        xd = (xd - xd.mean(-1, keepdim=True)) * torch.rsqrt(xd.var(-1, unbiased=False, keepdim=True) + eps)
    elif norm in ("rms", "rms_fp32"):
        xd = xd * torch.rsqrt(xd.square().mean(-1, keepdim=True) + eps)
    if weight is not None:
        w = weight.float() + weight_offset
        if weight_round:
            w = w.to(x.dtype)
        xd = xd * w.double()
    return xd


def _modulate_ref(h, scale, shift, scale_offset):
    term = h * (scale.double() + scale_offset)
    ref = term + (0 if shift is None else shift.double())
    return ref, term.abs() + (0 if shift is None else shift.double().abs())


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", WIDTHS)
def test_layer_norm_shift_and_scale_like_flux(dtype, width):
    x = _t(2, 77, width, dtype=dtype, scale=3.0)
    scale, shift = _t(2, 1, width, dtype=dtype, seed=1), _t(2, 1, width, dtype=dtype, seed=2)
    out = norm_modulate(x, norm="layer", eps=1e-6, scale=scale, shift=shift, addcmul=True)
    ref, mag = _modulate_ref(_norm_ref(x, "layer", 1e-6), scale, shift, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", WIDTHS)
def test_scale_only_layer_norm_like_qwen_image21(dtype, width):
    x = _t(1, 129, width, dtype=dtype)
    scale = _t(1, 1, width, dtype=dtype, seed=1)
    out = norm_modulate(x, norm="layer", eps=1e-6, scale=scale)
    ref, mag = _modulate_ref(_norm_ref(x, "layer", 1e-6), scale, None, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_weighted_rms_with_an_indexed_table_like_minimax_h3(dtype):
    width = 5376
    x = _t(1, 300, width, dtype=dtype, scale=2.0)
    weight = _t(width, dtype=dtype, seed=3)
    scale, shift = _t(12, width, dtype=dtype, seed=1), _t(12, width, dtype=dtype, seed=2)
    index = torch.randint(0, 12, (300,), device="cuda", generator=torch.Generator(device="cuda").manual_seed(4))
    out = norm_modulate(x, norm="rms", eps=1e-6, weight=weight, weight_cast=True, scale=scale, shift=shift, index=index)
    ref, mag = _modulate_ref(
        _norm_ref(x, "rms", 1e-6, weight, weight_round=True), scale.index_select(0, index), shift.index_select(0, index), 1.0,
    )
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_an_fp32_table_promotes_the_output_like_the_h3_final_layer(dtype):
    x = _t(1, 64, 3072, dtype=dtype)
    weight = _t(3072, dtype=dtype, seed=3)
    scale, shift = _t(4, 3072, seed=1), _t(4, 3072, seed=2)
    index = torch.arange(64, device="cuda") % 4
    out = norm_modulate(x, norm="rms", eps=1e-6, weight=weight, scale=scale, shift=shift, index=index)
    ref, mag = _modulate_ref(
        _norm_ref(x, "rms", 1e-6, weight, weight_round=True), scale.index_select(0, index), shift.index_select(0, index), 1.0,
    )
    _assert_one_rounding(out, ref, mag, torch.float32)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", [3072, 5120])
def test_offset_weight_rms_like_krea2(dtype, width):
    x = _t(2, 100, width, dtype=dtype)
    weight = _t(width, seed=3, scale=0.1)
    scale, shift = _t(2, 1, width, dtype=dtype, seed=1), _t(2, 1, width, dtype=dtype, seed=2)
    out = norm_modulate(x, norm="rms", eps=1e-5, weight=weight, weight_offset=1.0, scale=scale, shift=shift)
    ref, mag = _modulate_ref(_norm_ref(x, "rms", 1e-5, weight, 1.0, weight_round=True), scale, shift, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_fp32_normalised_rms_with_an_uncast_weight_like_moss(dtype):
    x = _t(1, 200, 3840, dtype=dtype)
    weight = _t(3840, dtype=dtype, seed=3)
    scale, shift = _t(1, 1, 3840, dtype=dtype, seed=1), _t(1, 1, 3840, dtype=dtype, seed=2)
    out = norm_modulate(x, norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False, scale=scale, shift=shift)
    ref, mag = _modulate_ref(_norm_ref(x, "rms_fp32", 1e-6, weight), scale, shift, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_unnormalised_context_modulation_like_ltx(dtype):
    x = _t(2, 33, 4096, dtype=dtype)
    scale, shift = _t(2, 1, 4096, dtype=dtype, seed=1), _t(2, 1, 4096, dtype=dtype, seed=2)
    out = norm_modulate(x, scale=scale, shift=shift)
    ref, mag = _modulate_ref(x.double(), scale, shift, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_five_dim_frame_broadcast_like_anima(dtype):
    x = _t(1, 3, 4, 5, 2048, dtype=dtype)
    scale, shift = _t(1, 3, 1, 1, 2048, dtype=dtype, seed=1), _t(1, 3, 1, 1, 2048, dtype=dtype, seed=2)
    out = norm_modulate(x, norm="layer", eps=1e-6, scale=scale, shift=shift)
    ref, mag = _modulate_ref(_norm_ref(x, "layer", 1e-6), scale, shift, 1.0)
    _assert_one_rounding(out, ref, mag, dtype)


def _span_ref_tables(tables, spans, batch):
    return torch.cat([t.expand(batch, n, t.shape[-1]) for t, n in zip(tables, spans)], dim=1)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("spans", [(4096, 512), (3, 0, 61), (17,)])
def test_span_modulation_and_gates_like_flux3_and_krea2(dtype, spans):
    width = 3072
    length = sum(spans)
    x, y = _t(2, length, width, dtype=dtype), _t(2, length, width, dtype=dtype, seed=9)
    scales = [_t(2, 1, width, dtype=dtype, seed=10 + i) for i in range(len(spans))]
    shifts = [_t(1, 1, width, dtype=dtype, seed=20 + i) for i in range(len(spans))]
    gates = [_t(2, 1, width, dtype=dtype, seed=30 + i) for i in range(len(spans))]
    out = norm_modulate(x, norm="layer", eps=1e-6, spans=spans, scale=scales, shift=shifts, addcmul=True)
    ref, mag = _modulate_ref(
        _norm_ref(x, "layer", 1e-6), _span_ref_tables(scales, spans, 2), _span_ref_tables(shifts, spans, 2), 1.0,
    )
    _assert_one_rounding(out, ref, mag, dtype)
    res = gated_residual(x, y, gates, spans=spans)
    gate = _span_ref_tables(gates, spans, 2).double()
    _assert_one_rounding(res, x.double() + y.double() * gate, x.double().abs() + (y.double() * gate).abs(), dtype)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", WIDTHS)
def test_plain_and_indexed_gated_residuals(dtype, width):
    x, y = _t(1, 300, width, dtype=dtype), _t(1, 300, width, dtype=dtype, seed=1)
    gate = _t(1, 1, width, dtype=dtype, seed=2)
    res = gated_residual(x, y, gate)
    term = y.double() * gate.double()
    _assert_one_rounding(res, x.double() + term, x.double().abs() + term.abs(), dtype)
    table = _t(7, width, dtype=dtype, seed=3)
    index = torch.arange(300, device="cuda") % 7
    res = gated_residual(x, y, table, index=index)
    term = y.double() * table.index_select(0, index).double()
    _assert_one_rounding(res, x.double() + term, x.double().abs() + term.abs(), dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_an_fp32_gate_promotes_the_residual_like_wan(dtype):
    x, y = _t(1, 50, 5120, dtype=dtype), _t(1, 50, 5120, dtype=dtype, seed=1)
    gate = _t(1, 50, 5120, seed=2)
    res = gated_residual(x, y, gate, addcmul=True)
    term = y.double() * gate.double()
    _assert_one_rounding(res, x.double() + term, x.double().abs() + term.abs(), torch.float32)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", [3840, 5120])
def test_tanh_gate_after_a_weighted_rms_post_norm_like_z_image(dtype, width):
    x, y = _t(2, 99, width, dtype=dtype), _t(2, 99, width, dtype=dtype, seed=1, scale=5.0)
    gate, weight = _t(2, 1, width, dtype=dtype, seed=2), _t(width, dtype=dtype, seed=3)
    res = gated_residual(x, y, gate, gate_fn="tanh", norm="rms", eps=1e-5, weight=weight, weight_cast=True)
    term = _norm_ref(y, "rms", 1e-5, weight, weight_round=True) * gate.double().tanh()
    _assert_one_rounding(res, x.double() + term, x.double().abs() + term.abs(), dtype)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", [3072, 12288, 13824])
def test_activations(dtype, width):
    h = _t(37, 2 * width, dtype=dtype, scale=3.0)
    gate, value = h.double().chunk(2, dim=-1)
    silu = gate / (1 + torch.exp(-gate))
    _assert_one_rounding(act_gate(h, mode="swiglu"), silu * value, (silu * value).abs(), dtype)
    g, u = _t(37, width, dtype=dtype, seed=1, scale=3.0), _t(37, width, dtype=dtype, seed=2)
    gd = g.double()
    ref = gd / (1 + torch.exp(-gd)) * u.double()
    _assert_one_rounding(act_gate(g, mode="swiglu_split", up=u), ref, ref.abs(), dtype)
    tanh_ref = 0.5 * gd * (1 + torch.tanh(0.7978845608028654 * (gd + 0.044715 * gd ** 3)))
    _assert_one_rounding(act_gate(g, mode="gelu_tanh"), tanh_ref, gd.abs(), dtype)
    exact_ref = 0.5 * gd * (1 + torch.erf(gd / 2 ** 0.5))
    _assert_one_rounding(act_gate(g, mode="gelu"), exact_ref, gd.abs(), dtype)


def _kernels_launched(fn) -> int:
    def count(f):
        f()
        torch.cuda.synchronize()
        with profile(activities=[ProfilerActivity.CUDA]) as prof:
            f()
            torch.cuda.synchronize()
        return Counter(e.name for e in prof.events() if e.device_type == torch.autograd.DeviceType.CUDA)

    return sum((count(fn) - count(lambda: None)).values())


def test_each_fused_call_launches_exactly_one_kernel():
    x, y = _t(2, 300, 3072, dtype=torch.bfloat16), _t(2, 300, 3072, dtype=torch.bfloat16, seed=1)
    scale, shift = _t(2, 1, 3072, dtype=torch.bfloat16, seed=2), _t(2, 1, 3072, dtype=torch.bfloat16, seed=3)
    weight = _t(3072, dtype=torch.bfloat16, seed=4)
    table, index = _t(5, 3072, dtype=torch.bfloat16, seed=5), torch.arange(300, device="cuda") % 5
    calls = {
        "flux modulate": lambda: norm_modulate(x, norm="layer", eps=1e-6, scale=scale, shift=shift, addcmul=True),
        "h3 modulate": lambda: norm_modulate(x, norm="rms", eps=1e-6, weight=weight, scale=table, shift=table, index=index),
        "gate": lambda: gated_residual(x, y, scale),
        "sandwich gate": lambda: gated_residual(x, y, scale, gate_fn="tanh", norm="rms", eps=1e-5, weight=weight),
        "swiglu": lambda: act_gate(y, mode="swiglu"),
        "gelu": lambda: act_gate(y, mode="gelu_tanh"),
    }
    assert {name: _kernels_launched(fn) for name, fn in calls.items()} == {name: 1 for name in calls}


def test_the_fused_ops_trace_into_torch_compile_without_graph_breaks():
    x = _t(1, 64, 3072, dtype=torch.bfloat16)
    scale, shift = _t(1, 1, 3072, dtype=torch.bfloat16, seed=1), _t(1, 1, 3072, dtype=torch.bfloat16, seed=2)

    def block(t):
        h = norm_modulate(t, norm="layer", eps=1e-6, scale=scale, shift=shift)
        return gated_residual(t, act_gate(h, mode="gelu_tanh"), scale)

    torch._dynamo.reset()
    compiled = torch.compile(block, fullgraph=True)
    torch.testing.assert_close(compiled(x), block(x), rtol=0, atol=0)
