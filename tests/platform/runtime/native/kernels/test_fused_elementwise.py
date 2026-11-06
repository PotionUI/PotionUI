from __future__ import annotations

import pytest
import torch
import torch.nn.functional as F
from torch import nn

from src.platform.runtime.native import kernels
from src.platform.runtime.native.kernels import (
    ActGate,
    act_gate,
    disable_weight_init,
    fp8_ops,
    gated_residual,
    manual_cast,
    norm_args,
    norm_modulate,
)

DTYPES = [torch.float32, torch.bfloat16]
B, L, D = 2, 13, 72


def _g(seed: int):
    return torch.Generator().manual_seed(seed)


def _t(*shape, dtype=torch.float32, seed=0):
    return torch.randn(*shape, generator=_g(seed)).to(dtype)


def _exact(a: torch.Tensor, b: torch.Tensor) -> None:
    assert a.dtype == b.dtype
    assert a.shape == b.shape
    assert torch.equal(a, b)


@pytest.mark.parametrize("dtype", DTYPES)
def test_flux_layer_norm_addcmul_modulation_is_bit_exact(dtype):
    norm = disable_weight_init.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    x, scale, shift = _t(B, L, D, dtype=dtype), _t(B, 1, D, dtype=dtype, seed=1), _t(B, 1, D, dtype=dtype, seed=2)
    expected = torch.addcmul(shift, norm(x), (1 + scale))
    _exact(norm_modulate(x, norm="layer", eps=norm.eps, scale=scale, shift=shift, addcmul=True), expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_flux3_segment_modulation_and_gate_are_bit_exact(dtype):
    x = _t(B, L, D, dtype=dtype)
    y = _t(B, L, D, dtype=dtype, seed=9)
    segments = [
        (5, _t(B, 1, D, dtype=dtype, seed=1), _t(B, 1, D, dtype=dtype, seed=2), _t(B, 1, D, dtype=dtype, seed=3)),
        (8, _t(B, 1, D, dtype=dtype, seed=4), _t(B, 1, D, dtype=dtype, seed=5), _t(B, 1, D, dtype=dtype, seed=6)),
    ]
    normed = F.layer_norm(x, (x.shape[-1],), eps=1e-6)
    parts, gates, start = [], [], 0
    for length, shift, scale, gate in segments:
        parts.append(torch.addcmul(shift, 1.0 + scale, normed[:, start : start + length]))
        gates.append(y[:, start : start + length] * gate)
        start += length
    spans = [s[0] for s in segments]
    got = norm_modulate(
        x, norm="layer", eps=1e-6, spans=spans, addcmul=True,
        scale=[s[2] for s in segments], shift=[s[1] for s in segments],
    )
    _exact(got, torch.cat(parts, dim=1))
    _exact(gated_residual(x, y, [s[3] for s in segments], spans=spans), x + torch.cat(gates, dim=1))


@pytest.mark.parametrize("dtype", DTYPES)
def test_a_single_span_takes_the_same_path_as_one_segment(dtype):
    x, scale, shift = _t(B, L, D, dtype=dtype), _t(B, 1, D, dtype=dtype, seed=1), _t(B, 1, D, dtype=dtype, seed=2)
    normed = F.layer_norm(x, (D,), eps=1e-6)
    expected = torch.addcmul(shift, 1.0 + scale, normed[:, 0:L])
    _exact(norm_modulate(x, norm="layer", spans=[L], scale=[scale], shift=[shift], addcmul=True), expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_wan_fp32_modulation_over_a_low_precision_stream_is_bit_exact(dtype):
    norm = manual_cast.LayerNorm(D, eps=1e-6, elementwise_affine=False)
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=7)
    e = [_t(B, L, D, seed=s) for s in range(3)]
    expected_h = torch.addcmul(e[0], norm(x), 1 + e[1])
    _exact(norm_modulate(x, norm="layer", eps=norm.eps, scale=e[1], shift=e[0], addcmul=True), expected_h)
    _exact(gated_residual(x, y, e[2], addcmul=True), torch.addcmul(x, y, e[2]))


@pytest.mark.parametrize("dtype", DTYPES)
def test_qwen_image_zero_timestep_spans_are_bit_exact(dtype):
    norm = disable_weight_init.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    x, out = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=8)
    shift, scale, gate = (_t(2 * B, D, dtype=dtype, seed=s) for s in (1, 2, 3))
    tzi = 6
    h = norm(x)
    real = torch.addcmul(shift[:B].unsqueeze(1), h[:, :tzi], 1 + scale[:B].unsqueeze(1))
    zero = torch.addcmul(shift[B:].unsqueeze(1), h[:, tzi:], 1 + scale[B:].unsqueeze(1))
    got = norm_modulate(
        x, norm="layer", eps=norm.eps, addcmul=True, spans=(tzi, L - tzi),
        scale=(scale[:B].unsqueeze(1), scale[B:].unsqueeze(1)),
        shift=(shift[:B].unsqueeze(1), shift[B:].unsqueeze(1)),
    )
    _exact(got, torch.cat([real, zero], dim=1))
    g_real, g_zero = gate[:B].unsqueeze(1), gate[B:].unsqueeze(1)
    expected = x + torch.cat([out[:, :tzi] * g_real, out[:, tzi:] * g_zero], dim=1)
    _exact(gated_residual(x, out, (g_real, g_zero), spans=(tzi, L - tzi)), expected)
    _exact(gated_residual(x, out, g_real, addcmul=True), torch.addcmul(x, g_real, out))


@pytest.mark.parametrize("dtype", DTYPES)
def test_qwen_image_last_layer_modulation_is_bit_exact(dtype):
    norm = disable_weight_init.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    x, scale, shift = _t(B, L, D, dtype=dtype), _t(B, D, dtype=dtype, seed=1), _t(B, D, dtype=dtype, seed=2)
    expected = torch.addcmul(shift[:, None, :], norm(x), (1 + scale)[:, None, :])
    got = norm_modulate(x, norm="layer", eps=norm.eps, scale=scale[:, None, :], shift=shift[:, None, :], addcmul=True)
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_qwen_image21_scale_only_prefix_modulation_is_bit_exact(dtype):
    norm = disable_weight_init.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=5)
    s_prefix, s_target = _t(B, 1, D, dtype=dtype, seed=1), _t(B, 1, D, dtype=dtype, seed=2)
    g_prefix, g_target = _t(B, 1, D, dtype=dtype, seed=3), _t(B, 1, D, dtype=dtype, seed=4)
    p = 4
    out = norm(x)
    expected = torch.cat([out[:, :p] * (1 + s_prefix), out[:, p:] * (1 + s_target)], dim=1)
    got = norm_modulate(x, norm="layer", eps=norm.eps, scale=(s_prefix, s_target), spans=(p, L - p))
    _exact(got, expected)
    _exact(norm_modulate(x, norm="layer", eps=norm.eps, scale=s_target), out * (1 + s_target))
    expected_res = x + torch.cat([y[:, :p] * g_prefix, y[:, p:] * g_target], dim=1)
    _exact(gated_residual(x, y, (g_prefix, g_target), spans=(p, L - p)), expected_res)
    _exact(gated_residual(x, y, g_target), x + y * g_target)


@pytest.mark.parametrize("ops", [disable_weight_init, manual_cast])
@pytest.mark.parametrize("dtype", DTYPES)
def test_z_image_sandwich_norm_with_tanh_gate_is_bit_exact(ops, dtype):
    norm1 = ops.RMSNorm(D, eps=1e-5, elementwise_affine=True, dtype=dtype)
    norm2 = ops.RMSNorm(D, eps=1e-5, elementwise_affine=True, dtype=dtype)
    with torch.no_grad():
        norm1.weight.copy_(_t(D, seed=11))
        norm2.weight.copy_(_t(D, seed=12))
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=3)
    scale, gate = _t(B, D, dtype=dtype, seed=1), _t(B, D, dtype=dtype, seed=2)
    expected_h = norm1(x) * (1 + scale.unsqueeze(1))
    _exact(norm_modulate(x, **norm_args(norm1), scale=scale.unsqueeze(1)), expected_h)
    expected = x + gate.unsqueeze(1).tanh() * norm2(y)
    _exact(gated_residual(x, y, gate.unsqueeze(1), gate_fn="tanh", **norm_args(norm2)), expected)


def test_a_cast_rms_norm_casts_its_storage_weight_like_the_module():
    norm = manual_cast.RMSNorm(D, eps=1e-6, elementwise_affine=True, dtype=torch.float32)
    with torch.no_grad():
        norm.weight.copy_(_t(D, seed=4))
    x, scale = _t(B, L, D, dtype=torch.bfloat16), _t(B, 1, D, dtype=torch.bfloat16, seed=1)
    _exact(norm_modulate(x, **norm_args(norm), scale=scale), norm(x) * (1 + scale))


def test_an_uncast_rms_norm_keeps_its_weight_dtype_like_the_module():
    norm = disable_weight_init.RMSNorm(D, eps=1e-6, elementwise_affine=True, dtype=torch.float32)
    with torch.no_grad():
        norm.weight.copy_(_t(D, seed=4))
    x, scale = _t(B, L, D, dtype=torch.bfloat16), _t(B, 1, D, dtype=torch.bfloat16, seed=1)
    _exact(norm_modulate(x, **norm_args(norm), scale=scale), norm(x) * (1 + scale))


@pytest.mark.parametrize("dtype", DTYPES)
def test_a_tanh_gate_applies_per_span(dtype):
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=3)
    g1, g2 = _t(B, 1, D, dtype=dtype, seed=1), _t(B, 1, D, dtype=dtype, seed=2)
    expected = x + torch.cat([y[:, :4] * g1.tanh(), y[:, 4:] * g2.tanh()], dim=1)
    _exact(gated_residual(x, y, (g1, g2), gate_fn="tanh", spans=(4, L - 4)), expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_anima_five_dim_modulation_and_gates_are_bit_exact(dtype):
    norm = nn.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    x, attn = _t(B, 3, 2, 2, D, dtype=dtype), _t(B, 3, 2, 2, D, dtype=dtype, seed=6)
    scale, shift, gate = (_t(B, 3, 1, 1, D, dtype=dtype, seed=s) for s in (1, 2, 3))
    _exact(norm_modulate(x, **norm_args(norm), scale=scale, shift=shift), norm(x) * (1 + scale) + shift)
    _exact(gated_residual(x, attn, gate), x + gate * attn)
    _exact(gated_residual(x, attn, gate), attn * gate + x)


@pytest.mark.parametrize("ops", [disable_weight_init, fp8_ops])
@pytest.mark.parametrize("dtype", DTYPES)
def test_minimax_h3_indexed_table_modulation_is_bit_exact(ops, dtype):
    norm = ops.RMSNorm(D, eps=1e-6, dtype=dtype)
    with torch.no_grad():
        norm.weight.copy_(_t(D, seed=5))
    x, h_out = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=8)
    shift, scale, gate = (_t(9, D, seed=s).to(dtype) for s in (1, 2, 3))
    idx = torch.randint(0, 9, (L,), generator=_g(4))
    h = norm(x)
    expected = h * (1.0 + scale.index_select(0, idx)) + shift.index_select(0, idx)
    _exact(norm_modulate(x, **norm_args(norm), scale=scale, shift=shift, index=idx), expected)
    _exact(gated_residual(x, h_out, gate, index=idx), x + gate.index_select(0, idx) * h_out)


@pytest.mark.parametrize("dtype", DTYPES)
def test_minimax_h3_final_layer_with_an_fp32_table_is_bit_exact(dtype):
    norm = disable_weight_init.RMSNorm(D, eps=1e-6, dtype=dtype)
    with torch.no_grad():
        norm.weight.copy_(_t(D, seed=5))
    x = _t(B, L, D, dtype=dtype)
    shift, scale = _t(4, D, seed=1), _t(4, D, seed=2)
    idx = torch.randint(0, 4, (L,), generator=_g(3))
    expected = norm(x) * (1.0 + scale.index_select(0, idx)) + shift.index_select(0, idx)
    _exact(norm_modulate(x, **norm_args(norm), scale=scale, shift=shift, index=idx), expected)


def _krea2_rms(x, scale, eps):
    dtype = x.dtype
    return F.rms_norm(x, (x.shape[-1],), eps=eps, weight=(scale + 1.0).to(dtype)).to(dtype)


def _krea2_args(scale, eps):
    return {"norm": "rms", "eps": eps, "weight": scale, "weight_offset": 1.0}


@pytest.mark.parametrize("dtype", DTYPES)
def test_krea2_offset_weight_rms_modulation_is_bit_exact(dtype):
    w = _t(D, seed=10) * 0.1
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=6)
    scale, shift, gate = (_t(B, 1, D, dtype=dtype, seed=s) for s in (1, 2, 3))
    expected = _krea2_rms(x, w, 1e-5) * (scale + 1) + shift
    _exact(norm_modulate(x, **_krea2_args(w, 1e-5), scale=scale, shift=shift), expected)
    _exact(gated_residual(x, y, gate), x + y * gate)


@pytest.mark.parametrize("dtype", DTYPES)
def test_krea2_reference_span_modulation_is_bit_exact(dtype):
    w = _t(D, seed=10) * 0.1
    x, y = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=6)
    main = [_t(B, 1, D, dtype=dtype, seed=s) for s in (1, 2, 3)]
    ref = [_t(B, 1, D, dtype=dtype, seed=s) for s in (4, 5, 6)]
    a, b = 3, 9
    h = _krea2_rms(x, w, 1e-5)
    expected = torch.cat((
        h[:, :a] * (main[0] + 1) + main[1],
        h[:, a:b] * (ref[0] + 1) + ref[1],
        h[:, b:] * (main[0] + 1) + main[1],
    ), dim=1)
    spans = (a, b - a, L - b)
    got = norm_modulate(
        x, **_krea2_args(w, 1e-5), spans=spans,
        scale=(main[0], ref[0], main[0]), shift=(main[1], ref[1], main[1]),
    )
    _exact(got, expected)
    expected_res = x + torch.cat((y[:, :a] * main[2], y[:, a:b] * ref[2], y[:, b:] * main[2]), dim=1)
    _exact(gated_residual(x, y, (main[2], ref[2], main[2]), spans=spans), expected_res)


@pytest.mark.parametrize("dtype", DTYPES)
def test_ltx_unweighted_rms_and_context_modulation_are_bit_exact(dtype):
    x, ctx = _t(B, L, D, dtype=dtype), _t(B, 5, D, dtype=dtype, seed=7)
    scale, shift = _t(B, L, D, dtype=dtype, seed=1), _t(B, L, D, dtype=dtype, seed=2)
    expected = F.rms_norm(x, (x.shape[-1],), eps=1e-6) * (1 + scale) + shift
    _exact(norm_modulate(x, norm="rms", eps=1e-6, scale=scale, shift=shift), expected)
    kv_scale, kv_shift = _t(B, 1, D, dtype=dtype, seed=3), _t(B, 1, D, dtype=dtype, seed=4)
    _exact(norm_modulate(ctx, scale=kv_scale, shift=kv_shift), ctx * (1 + kv_scale) + kv_shift)


@pytest.mark.parametrize("dtype", DTYPES)
def test_moss_fp32_normalised_rms_modulation_is_bit_exact(dtype):
    weight = _t(D, dtype=dtype, seed=9)
    x = _t(B, L, D, dtype=dtype)
    scale, shift = _t(B, D, dtype=dtype, seed=1), _t(B, D, dtype=dtype, seed=2)
    xf = x.float()
    normed = xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + 1e-6)
    expected = (normed.to(x.dtype) * weight) * (1 + scale[:, None]) + shift[:, None]
    got = norm_modulate(
        x, norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False, scale=scale[:, None], shift=shift[:, None],
    )
    _exact(got, expected)


def test_moss_keeps_an_uncast_weight_promoting_like_the_module():
    weight = _t(D, seed=9)
    x = _t(B, L, D, dtype=torch.bfloat16)
    xf = x.float()
    expected = (xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + 1e-6)).to(x.dtype) * weight
    got = norm_modulate(x, norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False, scale=torch.zeros(1), scale_offset=1.0)
    assert got.dtype == torch.float32
    _exact(got, expected * (1 + torch.zeros(1)))


@pytest.mark.parametrize("dtype", DTYPES)
def test_seedvr2_raw_scale_modulation_stays_in_place_and_bit_exact(dtype):
    hid = _t(L, D, dtype=dtype)
    sa, sb, ha, hb = (_t(L, D, dtype=dtype, seed=s) for s in (1, 2, 3, 4))
    expected = hid.clone().mul_(sa + sb).add_(ha + hb)
    got = norm_modulate(hid, scale=sa + sb, shift=ha + hb, scale_offset=0.0, inplace=True)
    assert got.data_ptr() == hid.data_ptr()
    _exact(got, expected)


def test_a_zero_scale_offset_does_not_touch_negative_zero_scales():
    h = torch.ones(4)
    scale = torch.tensor([-0.0, 0.0, -0.0, 2.0])
    got = norm_modulate(h, scale=scale, scale_offset=0.0)
    assert torch.equal(torch.signbit(got), torch.signbit(h * scale))


@pytest.mark.parametrize("dtype", DTYPES)
def test_swiglu_gate_is_the_first_half_like_every_fused_family(dtype):
    h = _t(B, L, 2 * D, dtype=dtype)
    x1, x2 = h.chunk(2, dim=-1)
    _exact(act_gate(h, mode="swiglu"), nn.SiLU()(x1) * x2)
    gate, up = h.chunk(2, dim=-1)
    _exact(act_gate(h, mode="swiglu"), F.silu(gate) * up)
    _exact(ActGate("swiglu")(h), F.silu(gate) * up)


@pytest.mark.parametrize("dtype", DTYPES)
def test_split_swiglu_and_both_gelus_are_bit_exact(dtype):
    g, u = _t(B, L, D, dtype=dtype), _t(B, L, D, dtype=dtype, seed=1)
    _exact(act_gate(g, mode="swiglu_split", up=u), F.silu(g) * u)
    _exact(act_gate(g, mode="gelu_tanh"), nn.GELU(approximate="tanh")(g))
    _exact(ActGate("gelu_tanh")(g), F.gelu(g, approximate="tanh"))
    _exact(act_gate(g, mode="gelu"), nn.GELU()(g))
    _exact(ActGate("gelu")(g), nn.GELU()(g))


def test_activation_module_rejects_the_two_input_mode_and_unknown_modes():
    with pytest.raises(ValueError):
        ActGate("swiglu_split")
    with pytest.raises(ValueError):
        ActGate("situ_split")
    with pytest.raises(ValueError):
        ActGate("relu")
    with pytest.raises(ValueError):
        act_gate(torch.ones(2), mode="relu")


def test_unknown_norm_and_gate_kinds_are_rejected():
    with pytest.raises(ValueError):
        norm_modulate(torch.ones(2, 4), norm="group", scale=torch.ones(4))
    with pytest.raises(ValueError):
        gated_residual(torch.ones(2, 4), torch.ones(2, 4), torch.ones(4), gate_fn="sigmoid")


def test_norm_args_reads_kind_eps_weight_and_cast_mode_from_the_module():
    rms = manual_cast.RMSNorm(D, eps=1e-5)
    assert norm_args(rms) == {"norm": "rms", "eps": 1e-5, "weight": rms.weight, "weight_cast": True}
    ln = disable_weight_init.LayerNorm(D, elementwise_affine=False, eps=1e-6)
    assert norm_args(ln) == {"norm": "layer", "eps": 1e-6, "weight": None, "weight_cast": False}
    plain = nn.LayerNorm(D, elementwise_affine=False)
    assert norm_args(plain)["weight_cast"] is False
    with pytest.raises(TypeError):
        norm_args(nn.LayerNorm(D))
    with pytest.raises(TypeError):
        norm_args(nn.GroupNorm(2, D))


@pytest.mark.parametrize("ops", [disable_weight_init, manual_cast, fp8_ops])
def test_every_operations_namespace_carries_the_fused_functions(ops):
    assert ops.norm_modulate is norm_modulate
    assert ops.gated_residual is gated_residual
    assert ops.act_gate is act_gate
    assert ops.norm_args is norm_args
    assert ops.ActGate is ActGate
    assert ops.qk_norm_rope is kernels.qk_norm_rope
    assert ops.apply_rope is kernels.apply_rope


def test_the_fused_functions_are_public_kernel_exports():
    for name in ("norm_modulate", "gated_residual", "act_gate", "norm_args", "ActGate", "qk_norm_rope", "apply_rope"):
        assert name in kernels.__all__


def _apply_rope1(x, freqs_cis):
    x_ = x.to(dtype=freqs_cis.dtype).reshape(*x.shape[:-1], -1, 1, 2)
    x_out = freqs_cis[..., 0] * x_[..., 0]
    x_out = x_out.addcmul(freqs_cis[..., 1], x_[..., 1])
    return x_out.reshape(*x.shape).type_as(x)


def _rope_matrix(*lead, half, seed=40):
    angle = _t(*lead, half, seed=seed) * 3.0
    return torch.stack([angle.cos(), -angle.sin(), angle.sin(), angle.cos()], dim=-1).reshape(*lead, half, 2, 2)


@pytest.mark.parametrize("dtype", DTYPES)
def test_flux_per_stream_qk_norm_and_rope_match_rope_after_the_concat(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    hd, txt, img = 32, 5, 9
    scale = _t(hd, seed=1)
    tq, iq, v = _t(B, 2, txt, hd, dtype=dtype, seed=2), _t(B, 2, img, hd, dtype=dtype, seed=3), _t(1, dtype=dtype)
    pe = _rope_matrix(1, 1, txt + img, half=hd // 2)

    def old_norm(x):
        weight = scale.to(dtype=x.dtype, device=x.device)
        return F.rms_norm(x, weight.shape, weight=weight, eps=1e-6).to(v)

    expected = _apply_rope1(torch.cat((old_norm(tq), old_norm(iq)), dim=2), pe)
    args = dict(norm="rms", eps=1e-6, weight=scale, cast_to=v.dtype, layout="pair_matrix")
    got = torch.cat((
        qk_norm_rope(tq, freqs=pe[:, :, :txt], **args), qk_norm_rope(iq, freqs=pe[:, :, txt:], **args),
    ), dim=2)
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_flux3_fp32_chained_qk_norm_and_cos_sin_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, scale = _t(B, 3, L, 64, dtype=dtype), _t(64, dtype=dtype, seed=1)
    angle = _t(1, 1, L, 32, seed=2) * 3.0
    cos, sin = angle.cos(), angle.sin()
    b, h, n, d = x.shape
    x_f = x.float()
    normed = x_f * x_f.pow(2).mean(-1, keepdim=True).add(1e-6).rsqrt() * scale.float()
    pairs = normed.reshape(b, h, n, d // 2, 2)
    first, second = pairs[..., 0], pairs[..., 1]
    expected = torch.stack((cos * first - sin * second, sin * first + cos * second), dim=-1).reshape(b, h, n, d).to(x.dtype)
    got = qk_norm_rope(x, norm="rms_fp32_chain", eps=1e-6, weight=scale, freqs=(cos, sin), layout="pair_cos_sin", out_dtype=x.dtype)
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_krea2_offset_weight_qk_norm_and_in_place_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, scale = _t(B, 2, L, 32, dtype=dtype), _t(32, seed=1) * 0.1
    freqs = _rope_matrix(B, L, half=16)
    h = F.rms_norm(x, (32,), eps=1e-5, weight=(scale + 1.0).to(dtype)).to(dtype)
    table = freqs[:, None, :, :, :]
    cos, sin = table[..., 0, 0].to(h.dtype), table[..., 1, 0].to(h.dtype)
    pair = h.reshape(*h.shape[:-1], -1, 2)
    x0, x1 = pair[..., 0].clone(), pair[..., 1]
    pair[..., 0].mul_(cos).sub_(x1 * sin)
    pair[..., 1].mul_(cos).add_(x0 * sin)
    got = qk_norm_rope(x, norm="rms", eps=1e-5, weight=scale, weight_offset=1.0, freqs=freqs, layout="pair_matrix_inplace")
    _exact(got, h)


@pytest.mark.parametrize("ops", [disable_weight_init, manual_cast])
@pytest.mark.parametrize("dtype", DTYPES)
def test_operations_rms_qk_norm_and_matrix_rope_like_z_image_and_qwen_are_bit_exact(ops, dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    norm = ops.RMSNorm(64, eps=1e-6, elementwise_affine=True, dtype=dtype)
    with torch.no_grad():
        norm.weight.copy_(_t(64, seed=1))
    x = _t(B, L, 4, 64, dtype=dtype)
    pe = _rope_matrix(B, L, 1, half=32)
    got = qk_norm_rope(x, **norm_args(norm), freqs=pe, layout="pair_matrix")
    _exact(got, _apply_rope1(norm(x), pe))


@pytest.mark.parametrize("dtype", DTYPES)
def test_moss_fp32_rms_and_rotate_half_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(B, L, 4, 32, dtype=dtype), _t(32, dtype=dtype, seed=1)
    rope = torch.cat((_t(1, L, 16, seed=2).sin(), _t(1, L, 16, seed=3).cos()), -1)
    xf = x.float()
    normed = (xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + 1e-6)).to(x.dtype) * weight
    sin, cos = rope.chunk(2, -1)
    sin = torch.cat((sin, sin), -1).unsqueeze(-2)
    cos = torch.cat((cos, cos), -1).unsqueeze(-2)
    first, second = normed.chunk(2, -1)
    expected = normed * cos + torch.cat((-second, first), -1) * sin
    got = qk_norm_rope(x, norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False, freqs=rope, layout="rotate_half")
    _exact(got, expected)


def test_qk_norm_rope_without_a_table_is_just_the_norm_and_unknown_layouts_are_rejected():
    from src.platform.runtime.native.kernels import apply_rope, qk_norm_rope

    x = _t(B, 2, L, 32, dtype=torch.bfloat16)
    _exact(qk_norm_rope(x, norm="rms", eps=1e-6, freqs=None, layout="pair_matrix"), F.rms_norm(x, (32,), eps=1e-6))
    with pytest.raises(ValueError):
        apply_rope(x, _t(1), layout="spiral")


def _rms(width, dtype, ops=disable_weight_init, seed=1):
    norm = ops.RMSNorm(width, eps=1e-6, elementwise_affine=True, dtype=dtype)
    with torch.no_grad():
        norm.weight.copy_(_t(width, seed=seed))
    return norm


@pytest.mark.parametrize("ops", [disable_weight_init, manual_cast])
@pytest.mark.parametrize("dtype", DTYPES)
def test_minimax_h3_per_head_rms_and_partial_rotate_half_rope_are_bit_exact(ops, dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    norm = _rms(40, dtype, ops)
    x = _t(B, L, 2, 40, dtype=dtype)
    angle = torch.cat((_t(L, 9, seed=2),) * 2, -1) * 3.0
    cos, sin = angle.cos().to(dtype), angle.sin().to(dtype)
    h = norm(x)
    rotary_dim = cos.shape[-1]
    x_rot, x_pass = h[..., :rotary_dim], h[..., rotary_dim:]
    c, s = cos.to(h.dtype)[None, :, None, :], sin.to(h.dtype)[None, :, None, :]
    x1, x2 = x_rot.chunk(2, dim=-1)
    expected = torch.cat((x_rot * c + torch.cat((-x2, x1), dim=-1) * s, x_pass), dim=-1)
    got = qk_norm_rope(x, **norm_args(norm), freqs=(cos[None, :, None, :], sin[None, :, None, :]), layout="half_rotate")
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_ltx_full_width_rms_and_interleaved_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    norm = _rms(32, dtype)
    x = _t(B, L, 32, dtype=dtype)
    angle = _t(B, L, 15, seed=2) * 3.0
    cos = torch.cat((torch.ones(B, L, 2), angle.cos().repeat_interleave(2, dim=-1)), -1)
    sin = torch.cat((torch.zeros(B, L, 2), angle.sin().repeat_interleave(2, dim=-1)), -1)
    h = norm(x)
    t1, t2 = h.unflatten(-1, (-1, 2)).unbind(dim=-1)
    expected = h * cos + torch.stack((-t2, t1), dim=-1).flatten(-2) * sin
    _exact(qk_norm_rope(x, **norm_args(norm), freqs=(cos, sin), layout="pair_rotate"), expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_ltx_full_width_rms_and_per_head_split_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    heads, hd = 4, 16
    norm = _rms(heads * hd, dtype)
    x = _t(B, L, heads * hd, dtype=dtype)
    angle = _t(B, L, heads * hd // 2, seed=2) * 3.0
    cos = angle.cos().reshape(B, L, heads, hd // 2).swapaxes(1, 2)
    sin = angle.sin().reshape(B, L, heads, hd // 2).swapaxes(1, 2)
    split = norm(x).reshape(B, L, heads, -1).swapaxes(1, 2).unflatten(-1, (2, -1))
    first_half, second_half = split[..., :1, :], split[..., 1:, :]
    output = split * cos.unsqueeze(-2)
    output[..., :1, :].addcmul_(-sin.unsqueeze(-2), second_half)
    output[..., 1:, :].addcmul_(sin.unsqueeze(-2), first_half)
    expected = output.flatten(-2).swapaxes(1, 2).reshape(B, L, -1)
    got = qk_norm_rope(
        x, **norm_args(norm), freqs=(cos.transpose(1, 2), sin.transpose(1, 2)), layout="half_cos_sin", heads=heads,
    )
    _exact(got, expected)


@pytest.mark.parametrize("qk_norm", [True, False])
@pytest.mark.parametrize("dtype", DTYPES)
def test_wan_full_width_rms_and_per_head_matrix_rope_are_bit_exact(qk_norm, dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    heads, hd = 3, 16
    norm = _rms(heads * hd, dtype, manual_cast)
    x = _t(B, L, heads * hd, dtype=dtype)
    pe = _rope_matrix(1, L, 1, half=hd // 2)
    h = norm(x) if qk_norm else x
    expected = _apply_rope1(h.view(B, L, heads, hd), pe).reshape(B, L, heads * hd)
    got = qk_norm_rope(x, **(norm_args(norm) if qk_norm else {}), freqs=pe, layout="pair_matrix", heads=heads)
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_seedvr2_fp32_partial_interleaved_rope_is_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    norm = _rms(16, dtype)
    x = _t(L, 3, 16, dtype=dtype)
    freqs = (_t(L, 7, seed=2) * 3.0).repeat_interleave(2, dim=-1)
    t = norm(x).transpose(0, 1)
    t_float = t.float()
    t_mid, t_right = t_float[..., :14], t_float[..., 14:]
    pairs = t_mid.unflatten(-1, (-1, 2))
    turned = torch.stack((-pairs[..., 1], pairs[..., 0]), dim=-1).flatten(-2)
    t_mid = ((t_mid * freqs.cos() * 1.0) + (turned * freqs.sin() * 1.0)).to(t_float.dtype)
    expected = torch.cat((t_mid, t_right), dim=-1).to(t.dtype).transpose(0, 1)
    got = qk_norm_rope(
        x, **norm_args(norm), freqs=(freqs.cos()[:, None], freqs.sin()[:, None]), layout="pair_rotate",
        cast_to=torch.float32, out_dtype=x.dtype,
    )
    _exact(got, expected)


@pytest.mark.parametrize("dtype", DTYPES)
def test_anima_per_head_rms_and_half_split_matrix_rope_are_bit_exact(dtype):
    from src.platform.runtime.native.kernels import qk_norm_rope

    norm = _rms(32, dtype)
    x = _t(B, L, 2, 32, dtype=dtype)
    rope = _rope_matrix(1, L, 1, half=16)
    h = norm(x)
    t_ = h.reshape(*h.shape[:-1], 2, -1).movedim(-2, -1).unsqueeze(-2).float()
    t_out = rope[..., 0] * t_[..., 0] + rope[..., 1] * t_[..., 1]
    expected = t_out.movedim(-1, -2).reshape(*h.shape).type_as(h)
    _exact(qk_norm_rope(x, **norm_args(norm), freqs=rope, layout="half_matrix"), expected)



@pytest.mark.parametrize("dtype", DTYPES)
def test_moss_situ_packed_and_split_are_bit_exact(dtype):
    h = _t(B, L, 2 * D, dtype=dtype) * 40.0
    hidden, gate = h.chunk(2, -1)
    expected = (4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)) * (25.0 * torch.tanh(hidden / 25.0))
    _exact(act_gate(h, mode="situ"), expected)
    _exact(ActGate("situ")(h), expected)
    gate, hidden = _t(B, L, D, dtype=dtype, seed=1) * 40.0, _t(B, L, D, dtype=dtype, seed=2) * 40.0
    expected = (4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)) * (25.0 * torch.tanh(hidden / 25.0))
    _exact(act_gate(gate, mode="situ_split", up=hidden), expected)
