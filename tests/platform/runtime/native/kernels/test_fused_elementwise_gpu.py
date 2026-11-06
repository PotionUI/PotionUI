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


def test_the_fused_ops_trace_into_torch_compile_without_graph_breaks(monkeypatch):
    import torch._inductor.config as inductor_config

    monkeypatch.setattr(inductor_config, "compile_threads", 1)
    x = _t(1, 64, 3072, dtype=torch.bfloat16)
    scale, shift = _t(1, 1, 3072, dtype=torch.bfloat16, seed=1), _t(1, 1, 3072, dtype=torch.bfloat16, seed=2)

    def block(t):
        h = norm_modulate(t, norm="layer", eps=1e-6, scale=scale, shift=shift)
        return gated_residual(t, act_gate(h, mode="gelu_tanh"), scale)

    torch._dynamo.reset()
    compiled = torch.compile(block, fullgraph=True)
    torch.testing.assert_close(compiled(x), block(x), rtol=0, atol=0)


def _qk_ref(x, weight, offset, eps, norm, round_weight):
    xd = x.double()
    if norm:
        xd = xd * torch.rsqrt(xd.square().mean(-1, keepdim=True) + eps)
    if weight is not None:
        w = weight.float() + offset
        xd = xd * (w.to(x.dtype) if round_weight else w).double()
    return xd


def _pairs_rotated(h, cos, sin):
    a, b = h[..., 0::2], h[..., 1::2]
    out, mag = torch.empty_like(h), torch.empty_like(h)
    out[..., 0::2] = a * cos - b * sin
    out[..., 1::2] = b * cos + a * sin
    mag[..., 0::2] = (a * cos).abs() + (b * sin).abs()
    mag[..., 1::2] = (b * cos).abs() + (a * sin).abs()
    return out, mag


def _matrix(lead, half, seed):
    angle = _t(*lead, half, seed=seed) * 3.0
    return torch.stack([angle.cos(), -angle.sin(), angle.sin(), angle.cos()], dim=-1).reshape(*lead, half, 2, 2)


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("head_dim", [64, 128])
def test_matrix_rope_after_rms_like_flux_and_z_image(dtype, head_dim):
    from src.platform.runtime.native.kernels import qk_norm_rope

    weight = _t(head_dim, seed=1)
    for shape, lead in (((2, 24, 300, head_dim), (1, 1, 300)), ((2, 300, 24, head_dim), (2, 300, 1))):
        x = _t(*shape, dtype=dtype, scale=4.0)
        pe = _matrix(lead, head_dim // 2, 2)
        out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, cast_to=dtype, freqs=pe, layout="pair_matrix")
        h = _qk_ref(x, weight, 0.0, 1e-6, True, True)
        ref, mag = _pairs_rotated(h, pe[..., 0, 0].double(), pe[..., 1, 0].double())
        _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_a_strided_head_split_input_like_qwen_image21(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 77, 12, 128, dtype=dtype).transpose(1, 2)
    weight, pe = _t(128, dtype=dtype, seed=1), _matrix((1, 1, 77), 64, 2)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=pe, layout="pair_matrix")
    ref, mag = _pairs_rotated(_qk_ref(x, weight, 0.0, 1e-6, True, True), pe[..., 0, 0].double(), pe[..., 1, 0].double())
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_in_place_rope_with_rounded_tables_like_krea2(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 16, 200, 128, dtype=dtype)
    weight, freqs = _t(128, seed=1) * 0.1, _matrix((2, 200), 64, 2)
    out = qk_norm_rope(x, norm="rms", eps=1e-5, weight=weight, weight_offset=1.0, freqs=freqs, layout="pair_matrix_inplace")
    table = freqs[:, None]
    cos, sin = table[..., 0, 0].to(dtype).double(), table[..., 1, 0].to(dtype).double()
    ref, mag = _pairs_rotated(_qk_ref(x, weight, 1.0, 1e-5, True, True), cos, sin)
    _assert_one_rounding(out, ref, mag, dtype)
    plain = x.clone()
    result = qk_norm_rope(plain, freqs=freqs, layout="pair_matrix_inplace")
    ref, mag = _pairs_rotated(x.double(), cos, sin)
    assert result.data_ptr() == plain.data_ptr()
    _assert_one_rounding(result, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_fp32_chain_with_cos_sin_tables_like_flux3(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(1, 24, 333, 128, dtype=dtype, scale=3.0), _t(128, dtype=dtype, seed=1)
    angle = _t(1, 1, 333, 64, seed=2) * 3.0
    cos, sin = angle.cos(), angle.sin()
    out = qk_norm_rope(
        x, norm="rms_fp32_chain", eps=1e-6, weight=weight, freqs=(cos, sin), layout="pair_cos_sin", out_dtype=dtype,
    )
    ref, mag = _pairs_rotated(_qk_ref(x, weight, 0.0, 1e-6, True, False), cos.double(), sin.double())
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_rotate_half_with_an_fp32_table_like_moss(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(1, 250, 16, 128, dtype=dtype), _t(128, dtype=dtype, seed=1)
    angle = _t(1, 250, 64, seed=2) * 3.0
    rope = torch.cat((angle.sin(), angle.cos()), -1)
    out = qk_norm_rope(x, norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False, freqs=rope, layout="rotate_half")
    h = _qk_ref(x, weight, 0.0, 1e-6, True, False)
    sin, cos = angle.sin().double().unsqueeze(-2), angle.cos().double().unsqueeze(-2)
    a, b = h[..., :64], h[..., 64:]
    ref = torch.cat((a * cos - b * sin, b * cos + a * sin), -1)
    mag = torch.cat(((a * cos).abs() + (b * sin).abs(), (b * cos).abs() + (a * sin).abs()), -1)
    _assert_one_rounding(out, ref, mag, torch.float32)


def test_each_qk_norm_rope_call_launches_exactly_one_kernel():
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(2, 24, 300, 128, dtype=torch.bfloat16), _t(128, dtype=torch.bfloat16, seed=1)
    pe = _matrix((1, 1, 300), 64, 2)
    angle = _t(1, 1, 300, 64, seed=3)
    cos, sin = angle.cos(), angle.sin()
    per_sample = _matrix((2, 300), 64, 4)
    calls = {
        "matrix": lambda: qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=pe, layout="pair_matrix"),
        "cos sin": lambda: qk_norm_rope(x, norm="rms_fp32_chain", eps=1e-6, weight=weight, freqs=(cos, sin),
                                        layout="pair_cos_sin", out_dtype=torch.bfloat16),
        "in place": lambda: qk_norm_rope(x, norm="rms", eps=1e-5, weight=weight, weight_offset=1.0,
                                         freqs=per_sample, layout="pair_matrix_inplace"),
    }
    assert {name: _kernels_launched(fn) for name, fn in calls.items()} == {name: 1 for name in calls}


def _rotated(h, first, second, cos_a, sin_a, cos_b, sin_b):
    a, b = h[..., first], h[..., second]
    out, mag = h.clone(), h.abs()
    out[..., first] = a * cos_a - b * sin_a
    out[..., second] = b * cos_b + a * sin_b
    mag[..., first] = (a * cos_a).abs() + (b * sin_a).abs()
    mag[..., second] = (b * cos_b).abs() + (a * sin_b).abs()
    return out, mag


@pytest.mark.parametrize("dtype", DTYPES)
def test_partial_rotate_half_with_pass_through_channels_like_minimax_h3(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 300, 3 * 8, 128, dtype=dtype, scale=3.0)[:, :, ::3]
    weight = _t(128, dtype=dtype, seed=1)
    angle = _t(300, 96, seed=2) * 3.0
    cos, sin = angle.cos().to(dtype)[None, :, None], angle.sin().to(dtype)[None, :, None]
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=(cos, sin), layout="half_rotate")
    c, s = cos.double(), sin.double()
    ref, mag = _rotated(_qk_ref(x, weight, 0.0, 1e-6, True, True), slice(0, 48), slice(48, 96),
                        c[..., :48], s[..., :48], c[..., 48:], s[..., 48:])
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_interleaved_rope_over_a_full_width_norm_like_ltx(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(1, 333, 2048, dtype=dtype, scale=2.0), _t(2048, dtype=dtype, seed=1)
    angle = _t(1, 333, 2048, seed=2) * 3.0
    cos, sin = angle.cos(), angle.sin()
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=(cos, sin), layout="pair_rotate")
    c, s = cos.double(), sin.double()
    ref, mag = _rotated(_qk_ref(x, weight, 0.0, 1e-6, True, True), slice(0, None, 2), slice(1, None, 2),
                        c[..., 0::2], s[..., 0::2], c[..., 1::2], s[..., 1::2])
    _assert_one_rounding(out, ref, mag, torch.float32)


@pytest.mark.parametrize("dtype", DTYPES)
def test_per_head_split_rope_over_a_full_width_norm_like_ltx(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    heads, hd = 16, 128
    x, weight = _t(2, 200, heads * hd, dtype=dtype, scale=2.0), _t(heads * hd, dtype=dtype, seed=1)
    angle = (_t(1, 200, heads * hd // 2, seed=2) * 3.0).to(dtype)
    cos = angle.float().cos().to(dtype).reshape(1, 200, heads, hd // 2).swapaxes(1, 2)
    sin = angle.float().sin().to(dtype).reshape(1, 200, heads, hd // 2).swapaxes(1, 2)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=(cos.transpose(1, 2), sin.transpose(1, 2)),
                       layout="half_cos_sin", heads=heads)
    h = _qk_ref(x, weight, 0.0, 1e-6, True, True).unflatten(-1, (heads, hd))
    c, s = cos.transpose(1, 2).double(), sin.transpose(1, 2).double()
    ref, mag = _rotated(h, slice(0, hd // 2), slice(hd // 2, None), c, s, c, s)
    _assert_one_rounding(out, ref.flatten(-2), mag.flatten(-2), dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_per_head_matrix_rope_over_a_full_width_norm_like_wan(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    heads, hd = 40, 128
    x, weight = _t(1, 250, heads * hd, dtype=dtype, scale=2.0), _t(heads * hd, seed=1)
    pe = _matrix((1, 250, 1), hd // 2, 2)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=pe, layout="pair_matrix", heads=heads)
    h = _qk_ref(x, weight, 0.0, 1e-6, True, True).unflatten(-1, (heads, hd))
    ref, mag = _pairs_rotated(h, pe[..., 0, 0].double(), pe[..., 1, 0].double())
    _assert_one_rounding(out, ref.flatten(-2), mag.flatten(-2), dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_fp32_partial_interleaved_rope_cast_back_like_seedvr2(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(300, 3, 8, 128, dtype=dtype, scale=3.0).unbind(1)[0]
    weight = _t(128, dtype=dtype, seed=1)
    angle = (_t(300, 63, seed=2) * 3.0).repeat_interleave(2, dim=-1)
    cos, sin = angle.cos()[:, None], angle.sin()[:, None]
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=(cos, sin), layout="pair_rotate",
                       cast_to=torch.float32, out_dtype=dtype)
    c, s = cos.double(), sin.double()
    ref, mag = _rotated(_qk_ref(x, weight, 0.0, 1e-6, True, True), slice(0, 126, 2), slice(1, 126, 2),
                        c[..., 0::2], s[..., 0::2], c[..., 1::2], s[..., 1::2])
    _assert_one_rounding(out, ref, mag, dtype)


@pytest.mark.parametrize("dtype", DTYPES)
def test_half_split_matrix_rope_like_anima(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(1, 256, 16, 128, dtype=dtype, scale=3.0), _t(128, dtype=dtype, seed=1)
    pe = _matrix((1, 256, 1), 64, 2)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=pe, layout="half_matrix")
    c, s = pe[..., 0, 0].double(), pe[..., 1, 0].double()
    ref, mag = _rotated(_qk_ref(x, weight, 0.0, 1e-6, True, True), slice(0, 64), slice(64, None), c, s, c, s)
    _assert_one_rounding(out, ref, mag, dtype)


def test_each_new_rope_layout_launches_exactly_one_kernel():
    from src.platform.runtime.native.kernels import qk_norm_rope

    dtype = torch.bfloat16
    x, weight = _t(2, 300, 24, 128, dtype=dtype), _t(128, dtype=dtype, seed=1)
    flat, flat_weight = _t(2, 300, 2048, dtype=dtype), _t(2048, dtype=dtype, seed=1)
    angle = _t(1, 300, 1, 96, seed=2)
    partial = (angle.cos().to(dtype), angle.sin().to(dtype))
    row = (_t(1, 300, 2048, seed=3).cos(), _t(1, 300, 2048, seed=3).sin())
    split = (_t(1, 300, 16, 64, seed=4).cos(), _t(1, 300, 16, 64, seed=4).sin())
    matrix = _matrix((1, 300, 1), 64, 5)
    calls = {
        "partial rotate half": lambda: qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=partial,
                                                    layout="half_rotate"),
        "interleaved row": lambda: qk_norm_rope(flat, norm="rms", eps=1e-6, weight=flat_weight, freqs=row,
                                                layout="pair_rotate"),
        "split heads": lambda: qk_norm_rope(flat, norm="rms", eps=1e-6, weight=flat_weight, freqs=split,
                                            layout="half_cos_sin", heads=16),
        "half matrix": lambda: qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, freqs=matrix,
                                            layout="half_matrix"),
    }
    assert {name: _kernels_launched(fn) for name, fn in calls.items()} == {name: 1 for name in calls}


def _situ_ref(hidden, gate):
    g, h = gate.double(), hidden.double()
    return 4.0 * torch.tanh(g / 4.0) * torch.sigmoid(g) * (25.0 * torch.tanh(h / 25.0))


@pytest.mark.parametrize("dtype", DTYPES)
@pytest.mark.parametrize("width", [3072, 13824])
def test_moss_situ_packed_and_split(dtype, width):
    h = _t(37, 2 * width, dtype=dtype, scale=30.0)
    hidden, gate = h.chunk(2, dim=-1)
    ref = _situ_ref(hidden, gate)
    _assert_one_rounding(act_gate(h, mode="situ"), ref, ref.abs(), dtype)
    gate, hidden = _t(37, width, dtype=dtype, seed=1, scale=30.0), _t(37, width, dtype=dtype, seed=2, scale=30.0)
    ref = _situ_ref(hidden, gate)
    _assert_one_rounding(act_gate(gate, mode="situ_split", up=hidden), ref, ref.abs(), dtype)


def test_each_situ_call_launches_exactly_one_kernel():
    h = _t(2, 300, 2 * 3072, dtype=torch.bfloat16)
    gate, hidden = _t(2, 300, 3072, dtype=torch.bfloat16, seed=1), _t(2, 300, 3072, dtype=torch.bfloat16, seed=2)
    calls = {
        "situ": lambda: act_gate(h, mode="situ"),
        "situ split": lambda: act_gate(gate, mode="situ_split", up=hidden),
    }
    assert {name: _kernels_launched(fn) for name, fn in calls.items()} == {name: 1 for name in calls}
