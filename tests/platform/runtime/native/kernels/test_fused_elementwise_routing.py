from __future__ import annotations

import logging

import pytest
import torch

from src.platform.runtime.native.kernels import (
    Capabilities,
    act_gate,
    fused_elementwise,
    gated_residual,
    norm_modulate,
)
from src.platform.settings.runtime_flags import runtime_flag_values
from tests.fixtures.kernel_capabilities import cpu_only, fake_device, installed_capabilities

D = 64


class FakeKernels:
    def __init__(self):
        self.calls = []
        self.error = None

    def _record(self, name, args, out):
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        return out

    def fused_norm_modulate(self, x, scale, shift, weight, index, layout, norm, eps, w_offset, w_round, s_offset, out_dtype):
        return self._record("norm_modulate", locals(), torch.full(x.shape, 7.0, dtype=out_dtype))

    def fused_gated_residual(self, x, y, gate, weight, index, layout, norm, eps, w_round, tanh, out_dtype):
        return self._record("gated_residual", locals(), torch.full(x.shape, 7.0, dtype=out_dtype))

    def fused_qk_norm_rope(self, x, out, weight, t0, t1, layout, norm, eps, w_offset, w_round, rope):
        return self._record("qk_norm_rope", locals(), out)

    def fused_act_gate(self, h, up, mode):
        width = h.shape[-1] // 2 if mode in (0, 4) else h.shape[-1]
        return self._record("act_gate", locals(), torch.full((*h.shape[:-1], width), 7.0, dtype=h.dtype))


def _gpu_like_cpu(triton=True, capability=(9, 0), vendor="cuda"):
    return Capabilities(devices=(), cpu=fake_device(capability, index=None, vendor=vendor), triton=triton)


@pytest.fixture
def kernels(monkeypatch):
    fused_elementwise.reset_fused_elementwise_state()
    fake = FakeKernels()
    monkeypatch.setattr(fused_elementwise, "_kernels", fake)
    monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", True)
    with installed_capabilities(_gpu_like_cpu()):
        yield fake
    fused_elementwise.reset_fused_elementwise_state()


def _t(*shape, dtype=torch.bfloat16, seed=0):
    return torch.randn(*shape, generator=torch.Generator().manual_seed(seed)).to(dtype)


def _flux_args():
    return dict(norm="layer", eps=1e-6, scale=_t(2, 1, D, seed=1), shift=_t(2, 1, D, seed=2), addcmul=True)


def _eager(fn, *args, **kwargs):
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(runtime_flag_values(), "native_fused_elementwise", False)
        return fn(*args, **kwargs)


def test_a_flux_modulation_routes_with_the_table_broadcast_to_the_input(kernels):
    x = _t(2, 5, D)
    out = norm_modulate(x, **_flux_args())
    (name, args), = kernels.calls
    assert name == "norm_modulate" and torch.equal(out, torch.full(x.shape, 7.0, dtype=torch.bfloat16))
    assert args["scale"].shape == (2, 1, D) and args["shift"].shape == (2, 1, D)
    assert args["layout"] == [5, D, 0, 1, D, 0, 1]
    assert (args["norm"], args["eps"], args["s_offset"], args["index"], args["weight"]) == (1, 1e-6, 1.0, None, None)


def test_an_indexed_table_routes_with_its_rows_and_the_index(kernels):
    x = _t(1, 6, D)
    table, weight = _t(4, D, seed=1), _t(D, seed=2)
    index = torch.tensor([0, 1, 2, 3, 3, 0])
    norm_modulate(x, norm="rms", eps=None, weight=weight, scale=table, shift=table, index=index)
    (_, args), = kernels.calls
    assert args["scale"] is table
    assert args["index"] is index
    assert args["layout"] == [6, 0, D, 1, 0, D, 1]
    assert args["weight"] is weight and args["w_round"] is True and args["norm"] == 2
    assert args["eps"] == torch.finfo(torch.bfloat16).eps


def test_spans_become_one_stacked_table_and_a_cached_row_index(kernels):
    x = _t(2, 7, D)
    scales = [_t(2, 1, D, seed=1), _t(1, 1, D, seed=2)]
    norm_modulate(x, norm="layer", eps=1e-6, spans=(3, 4), scale=scales, addcmul=True)
    norm_modulate(x, norm="layer", eps=1e-6, spans=(3, 4), scale=scales, addcmul=True)
    first, second = (args for _, args in kernels.calls)
    assert first["scale"].shape == (2, 2, D)
    assert torch.equal(first["scale"][:, 1], scales[1].expand(2, 1, D)[:, 0])
    assert first["index"].tolist() == [0, 0, 0, 1, 1, 1, 1]
    assert second["index"] is first["index"]
    assert first["shift"] is None


def test_an_fp32_table_makes_the_fused_output_fp32_like_the_eager_promotion(kernels):
    x = _t(1, 3, D)
    out = norm_modulate(x, scale=_t(1, 1, D, dtype=torch.float32), shift=_t(1, 1, D, seed=1))
    assert out.dtype == torch.float32 == _eager(norm_modulate, x, scale=_t(1, 1, D, dtype=torch.float32), shift=_t(1, 1, D, seed=1)).dtype


def test_an_uncast_rms_fp32_weight_promotes_like_moss(kernels):
    x = _t(1, 3, D)
    norm_modulate(x, norm="rms_fp32", eps=1e-6, weight=_t(D, dtype=torch.float32), weight_cast=False, scale=_t(1, 1, D))
    (_, args), = kernels.calls
    assert args["out_dtype"] == torch.float32 and args["w_round"] is False


def test_a_sandwich_gate_routes_its_post_norm_and_tanh(kernels):
    x, y = _t(2, 5, D), _t(2, 5, D, seed=1)
    weight = _t(D, seed=2)
    gated_residual(x, y, _t(2, 1, D, seed=3), gate_fn="tanh", norm="rms", eps=1e-5, weight=weight)
    (name, args), = kernels.calls
    assert name == "gated_residual"
    assert (args["norm"], args["tanh"], args["w_round"], args["weight"]) == (2, True, True, weight)


def test_activations_route_with_their_mode_and_second_input(kernels):
    h, up = _t(3, 2 * D), _t(3, 2 * D, seed=1)
    assert act_gate(h, mode="swiglu").shape == (3, D)
    act_gate(h, mode="swiglu_split", up=up)
    act_gate(h, mode="gelu")
    modes = [(args["mode"], args["up"]) for _, args in kernels.calls]
    assert modes[0] == (0, None) and modes[1][0] == 1 and modes[1][1] is up and modes[2] == (3, None)


def _case(name):
    x = _t(2, 5, D)
    if name == "fp32 input":
        return lambda: norm_modulate(x.float(), **_flux_args())
    if name == "in place":
        return lambda: norm_modulate(x.clone(), scale=_t(2, 1, D, seed=1), shift=_t(2, 1, D), scale_offset=0.0, inplace=True)
    if name == "strided last dim":
        wide = _t(2, 5, 2 * D)
        return lambda: norm_modulate(wide[..., ::2], **_flux_args())
    if name == "non-contiguous residual branch":
        return lambda: gated_residual(x, _t(5, 2, D).transpose(0, 1), _t(2, 1, D))
    if name == "spans not covering the sequence":
        return lambda: norm_modulate(x, norm="layer", eps=1e-6, spans=(2, 2), scale=[_t(2, 1, D)] * 2)
    if name == "spans with an index":
        return lambda: norm_modulate(x, spans=(2, 3), scale=[_t(2, 1, D)] * 2, index=torch.zeros(5, dtype=torch.long))
    if name == "per-token span table":
        return lambda: norm_modulate(x, spans=(2, 3), scale=[_t(2, 2, D), _t(2, 3, D)])
    if name == "row wider than the norm limit":
        wide = _t(1, 2, fused_elementwise.FUSED_MAX_NORM_WIDTH + 16)
        return lambda: norm_modulate(wide, norm="rms", eps=1e-6, scale=_t(1, 1, wide.shape[-1]))
    if name == "index of the wrong length":
        return lambda: norm_modulate(x, scale=_t(3, D), index=torch.tensor([0, 1, 2]))
    if name == "non-broadcastable table":
        return lambda: gated_residual(x, x, _t(3, 1, D))
    if name == "layout that needs three strides":
        return lambda: gated_residual(_t(2, 3, 4, D), _t(2, 3, 4, D), _t(1, 3, 1, D))
    if name == "non-contiguous input":
        return lambda: norm_modulate(_t(5, 2, D).transpose(0, 1), **_flux_args())
    if name == "odd swiglu width":
        return lambda: act_gate(_t(3, 2 * D + 1), mode="swiglu")
    if name == "split swiglu with another dtype":
        return lambda: act_gate(_t(3, D), mode="swiglu_split", up=_t(3, D, dtype=torch.float16))
    if name == "odd situ width":
        return lambda: act_gate(_t(3, 2 * D + 1), mode="situ")
    if name == "split situ with another shape":
        return lambda: act_gate(_t(3, D), mode="situ_split", up=_t(3, 2 * D))
    if name == "non-contiguous activation":
        return lambda: act_gate(_t(2 * D, 3).t(), mode="gelu")
    if name == "unknown gate function":
        return lambda: pytest.raises(ValueError, gated_residual, x, x, _t(2, 1, D), gate_fn="sigmoid")
    raise AssertionError(name)


CASES = [
    "fp32 input", "in place", "strided last dim", "spans not covering the sequence", "spans with an index",
    "per-token span table", "row wider than the norm limit", "layout that needs three strides", "non-contiguous input",
    "non-contiguous residual branch", "index of the wrong length", "non-broadcastable table",
    "odd swiglu width", "split swiglu with another dtype", "non-contiguous activation", "unknown gate function",
    "odd situ width", "split situ with another shape",
]


@pytest.mark.parametrize("name", CASES)
def test_unsupported_variants_stay_on_the_eager_code(name, kernels):
    run = _case(name)
    try:
        run()
    except (RuntimeError, ValueError):
        pass
    assert kernels.calls == []


@pytest.mark.parametrize(
    "setup",
    ["flag off", "no triton", "cpu device", "pre-ampere gpu", "rocm gpu"],
)
def test_the_machine_or_the_flag_keeps_the_bit_exact_eager_code(setup, kernels, monkeypatch):
    x = _t(2, 5, D)
    want = _eager(norm_modulate, x, **_flux_args())
    caps = _gpu_like_cpu()
    if setup == "flag off":
        monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", False)
    elif setup == "no triton":
        caps = _gpu_like_cpu(triton=False)
    elif setup == "cpu device":
        caps = cpu_only()
    elif setup == "pre-ampere gpu":
        caps = _gpu_like_cpu(capability=(7, 5))
    elif setup == "rocm gpu":
        caps = _gpu_like_cpu(vendor="hip")
    with installed_capabilities(caps):
        got = norm_modulate(x, **_flux_args())
    assert kernels.calls == []
    assert torch.equal(got, want)


def test_a_kernel_failure_turns_the_triton_path_off_and_warns_once(kernels, caplog):
    kernels.error = RuntimeError("PTX assembly failed")
    x = _t(2, 5, D)
    want = _eager(norm_modulate, x, **_flux_args())
    with caplog.at_level(logging.WARNING, logger=fused_elementwise.__name__):
        first = norm_modulate(x, **_flux_args())
        second = norm_modulate(x, **_flux_args())
    assert torch.equal(first, want) and torch.equal(second, want)
    assert len(kernels.calls) == 1
    assert "PTX assembly failed" in fused_elementwise.fused_elementwise_disabled_reason()
    assert len([r for r in caplog.records if "Triton path is off" in r.getMessage()]) == 1


@pytest.mark.parametrize(
    "error",
    [torch.cuda.OutOfMemoryError("CUDA out of memory. Tried to allocate 2 GiB"),
     RuntimeError("CUDA driver error: out of memory")],
)
def test_out_of_memory_propagates_without_turning_the_path_off(error, kernels):
    kernels.error = error
    with pytest.raises(type(error)):
        norm_modulate(_t(2, 5, D), **_flux_args())
    assert fused_elementwise.fused_elementwise_disabled_reason() is None


def test_a_missing_triton_install_turns_the_path_off(kernels, monkeypatch):
    import sys

    monkeypatch.setattr(fused_elementwise, "_kernels", None)
    monkeypatch.setitem(sys.modules, "src.platform.runtime.native.kernels.fused_elementwise_triton", None)
    from src.platform.runtime.native import kernels as package

    monkeypatch.delattr(package, "fused_elementwise_triton", raising=False)
    x = _t(2, 5, D)
    assert torch.equal(norm_modulate(x, **_flux_args()), _eager(norm_modulate, x, **_flux_args()))
    assert "ModuleNotFoundError" in fused_elementwise.fused_elementwise_disabled_reason()


def _pe(lead, half):
    angle = _t(*lead, half, dtype=torch.float32, seed=9)
    return torch.stack([angle.cos(), -angle.sin(), angle.sin(), angle.cos()], dim=-1).reshape(*lead, half, 2, 2)


def test_a_flux_qk_norm_rope_routes_with_its_table_strides(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight, pe = _t(2, 3, 5, 16), _t(16, seed=1), _pe((1, 1, 5), 8)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=weight, cast_to=torch.bfloat16, freqs=pe, layout="pair_matrix")
    (name, args), = kernels.calls
    assert name == "qk_norm_rope" and out is args["out"] and out.dtype == torch.bfloat16 and out is not x
    assert (args["rope"], args["norm"], args["w_round"], args["t0"], args["t1"]) == (0, 1, True, pe, None)
    assert args["layout"] == [3, 5, 240, 80, 16, 240, 80, 16, 0, 0, 32, 4, 0, 0, 32, 0, 0, 1, 16, 16]


def test_an_unnormalised_in_place_rope_writes_into_its_input_like_krea2(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 3, 5, 16)
    out = qk_norm_rope(x, freqs=_pe((2, 5), 8), layout="pair_matrix_inplace")
    (_, args), = kernels.calls
    assert out is x and args["rope"] == 1 and args["norm"] == 0


def test_flux3_and_moss_tables_route_with_their_eager_output_dtypes(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, weight = _t(1, 2, 5, 16), _t(16, seed=1)
    angle = _t(1, 1, 5, 8, dtype=torch.float32, seed=2)
    qk_norm_rope(x, norm="rms_fp32_chain", eps=1e-6, weight=weight, freqs=(angle.cos(), angle.sin()),
                 layout="pair_cos_sin", out_dtype=torch.bfloat16)
    qk_norm_rope(_t(1, 5, 2, 16), norm="rms_fp32", eps=1e-6, weight=weight, weight_cast=False,
                 freqs=_t(1, 5, 16, dtype=torch.float32), layout="rotate_half")
    (_, chain), (_, moss) = kernels.calls
    assert chain["rope"] == 2 and chain["out"].dtype == torch.bfloat16 and chain["w_round"] is False
    assert moss["rope"] == 3 and moss["out"].dtype == torch.float32 and moss["w_round"] is False


@pytest.mark.parametrize("name", ["no table", "three dims", "five dims", "layer norm", "strided table pairs", "flag off"])
def test_unsupported_qk_calls_stay_on_the_bit_exact_eager_code(name, kernels, monkeypatch):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, pe = _t(2, 3, 5, 16), _pe((1, 1, 5), 8)
    kwargs = dict(norm="rms", eps=1e-6, freqs=pe, layout="pair_matrix")
    if name == "no table":
        kwargs["freqs"] = None
    elif name == "three dims":
        x = _t(3, 5, 16)
    elif name == "five dims":
        x = _t(1, 2, 3, 5, 16)
    elif name == "layer norm":
        kwargs["norm"] = "layer"
    elif name == "strided table pairs":
        kwargs["freqs"] = _pe((1, 1, 5), 8).transpose(-1, -2)
    elif name == "flag off":
        monkeypatch.setitem(runtime_flag_values(), "native_fused_elementwise", False)
    got = qk_norm_rope(x, **kwargs)
    assert kernels.calls == []
    assert torch.equal(got, _eager(qk_norm_rope, x, **kwargs))


def _cos_sin(*shape, seed=9, dtype=torch.float32):
    angle = _t(*shape, dtype=torch.float32, seed=seed)
    return angle.cos().to(dtype), angle.sin().to(dtype)


def test_a_partial_rotate_half_rope_like_minimax_h3_routes_with_its_pass_through_width(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 7, 3, 64)[..., :40]
    cos, sin = _cos_sin(1, 7, 1, 18, dtype=torch.bfloat16)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=_t(40, seed=1), freqs=(cos, sin), layout="half_rotate")
    (_, args), = kernels.calls
    assert args["rope"] == 5 and out.dtype == torch.bfloat16 and args["t0"] is cos and args["t1"] is sin
    assert args["layout"] == [7, 3, 1344, 192, 64, 840, 120, 40, 0, 18, 0, 1, 0, 18, 0, 0, 0, 1, 40, 18]


def test_a_full_width_norm_with_per_head_split_tables_like_ltx_routes_as_one_row_of_heads(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 5, 4 * 16)
    cos, sin = (t.reshape(1, 5, 4, 8).swapaxes(1, 2) for t in _cos_sin(1, 5, 32))
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=_t(64, seed=1), freqs=(cos.transpose(1, 2), sin.transpose(1, 2)),
                       layout="half_cos_sin", heads=4)
    (_, args), = kernels.calls
    assert args["rope"] == 6 and out.shape == x.shape and out.dtype == torch.float32
    assert args["layout"] == [2, 5, 0, 320, 64, 0, 320, 64, 0, 0, 32, 1, 0, 0, 32, 8, 8, 4, 16, 16]


def test_an_interleaved_rope_over_the_whole_row_like_ltx_routes_three_dim_input(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(2, 5, 64)
    cos, sin = _cos_sin(2, 5, 64)
    qk_norm_rope(x, norm="rms", eps=1e-6, weight=_t(64, seed=1), freqs=(cos, sin), layout="pair_rotate")
    (_, args), = kernels.calls
    assert args["rope"] == 4 and args["layout"][-3:] == [1, 64, 64]
    assert args["layout"][:5] == [2, 5, 0, 320, 64] and args["layout"][8:11] == [0, 320, 64]


def test_a_full_width_norm_with_a_shared_matrix_table_like_wan_routes_with_a_zero_head_stride(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, pe = _t(1, 5, 3 * 16), _pe((1, 5, 1), 8)
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=_t(48, seed=1), freqs=pe, layout="pair_matrix", heads=3)
    (_, args), = kernels.calls
    assert args["rope"] == 0 and out.dtype == torch.bfloat16 and args["norm"] == 1
    assert args["layout"][-5:] == [0, 0, 3, 16, 16] and args["layout"][8:12] == [0, 160, 32, 4]


def test_an_fp32_rope_cast_back_like_seedvr2_routes_with_its_requested_dtype(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x = _t(7, 3, 16)
    cos, sin = (t[:, None] for t in _cos_sin(7, 14))
    out = qk_norm_rope(x, norm="rms", eps=1e-6, weight=_t(16, seed=1), freqs=(cos, sin), layout="pair_rotate",
                       cast_to=torch.float32, out_dtype=torch.bfloat16)
    (_, args), = kernels.calls
    assert args["rope"] == 4 and out.dtype == torch.bfloat16 and args["layout"][-3:] == [1, 16, 14]


def test_a_half_split_matrix_rope_like_anima_routes_without_promoting(kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    out = qk_norm_rope(_t(1, 5, 2, 16), norm="rms", eps=1e-6, weight=_t(16, seed=1), freqs=_pe((1, 5, 1), 8),
                       layout="half_matrix")
    (_, args), = kernels.calls
    assert args["rope"] == 7 and out.dtype == torch.bfloat16


@pytest.mark.parametrize("name", ["odd rotary width", "five dims", "strided cos table", "row over the norm cap"])
def test_unsupported_rope_geometry_stays_on_the_bit_exact_eager_code(name, kernels):
    from src.platform.runtime.native.kernels import qk_norm_rope

    x, kwargs = _t(2, 5, 3, 16), dict(norm="rms", eps=1e-6, layout="half_rotate", freqs=_cos_sin(1, 5, 1, 12))
    if name == "odd rotary width":
        kwargs["freqs"] = _cos_sin(1, 5, 1, 11)
    elif name == "five dims":
        x = _t(2, 2, 5, 3, 16)
    elif name == "strided cos table":
        cos, sin = _cos_sin(1, 5, 1, 12, 2)
        kwargs["freqs"] = (cos[..., 0], sin[..., 0])
    elif name == "row over the norm cap":
        x, kwargs["layout"] = _t(1, 2, 16386), "pair_rotate"
        kwargs["freqs"] = _cos_sin(1, 2, 16386)
    got = qk_norm_rope(x, **kwargs)
    assert kernels.calls == []
    assert torch.equal(got, _eager(qk_norm_rope, x, **kwargs))


def test_situ_routes_packed_and_split_with_its_own_mode_codes(kernels):
    h, gate, hidden = _t(3, 2 * D), _t(3, D, seed=1), _t(3, D, seed=2)
    assert act_gate(h, mode="situ").shape == (3, D)
    act_gate(gate, mode="situ_split", up=hidden)
    (_, packed), (_, split) = kernels.calls
    assert (packed["mode"], packed["up"], packed["h"]) == (4, None, h)
    assert split["mode"] == 5 and split["h"] is gate and split["up"] is hidden
