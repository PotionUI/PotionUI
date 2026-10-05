from __future__ import annotations

import pytest
import torch
import torch.nn as nn
import torch.nn.functional as F

from src.platform.runtime.native.arch.krea2.model import Krea2
from src.platform.runtime.native.base import load_into_module
from src.platform.runtime.native.detect.registry import match_model_spec
from src.platform.runtime.native.engine import NativeModel
from src.platform.runtime.native.lora import LoraStepWindow, LoraStepWindowHook
from src.platform.runtime.native.lora.apply import apply_loras, remove_loras
from src.platform.runtime.native.lora.key_mapping import LoraDelta
from vendor.gpl.comfyui import ops

from tests.platform.runtime.native.lora.test_lora_fused_fast_path import (
    IN, OUT, _dequant, _delta, _fp8_fast, _fp8_layer, _input, _nvfp4_fast, _nvfp4_layer,
)
from tests.platform.runtime.native.lora.test_krea2_lora import TINY

CASES = {
    "full_w1_first": {"w1": (8, 8), "w2": (12, 8)},
    "full_w2_first": {"w1": (8, 4), "w2": (12, 16)},
    "lowrank_w2": {"w1": (4, 2), "w2": (24, 32, 3)},
    "lowrank_w1": {"w1": (6, 8, 2), "w2": (16, 8)},
    "lowrank_both": {"w1": (8, 4, 2), "w2": (12, 16, 3)},
}

PATHS = {
    "fp8_fast": (_fp8_layer, _fp8_fast, "fused-fast (+lokr)"),
    "nvfp4_fast": (_nvfp4_layer, _nvfp4_fast, "fused-fast (+lokr)"),
    "dequant": (_fp8_layer, _dequant, "dequant (fp8 matmul off) (+lokr)"),
}


@pytest.fixture(autouse=True)
def _fused_default(monkeypatch):
    monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV, raising=False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    ops._lora_path_layers.clear()
    yield
    ops._lora_path_layers.clear()


def _side(sd, prefix, spec, g):
    if len(spec) == 2:
        sd[f"proj.lokr_{prefix}"] = torch.randn(*spec, generator=g) * 0.3
    else:
        rows, cols, rank = spec
        sd[f"proj.lokr_{prefix}_a"] = torch.randn(rows, rank, generator=g) * 0.4
        sd[f"proj.lokr_{prefix}_b"] = torch.randn(rank, cols, generator=g) * 0.4


def _lokr_sd(case, alpha=None, seed=21):
    g = torch.Generator().manual_seed(seed)
    sd = {}
    _side(sd, "w1", CASES[case]["w1"], g)
    _side(sd, "w2", CASES[case]["w2"], g)
    if alpha is not None:
        sd["proj.alpha"] = torch.tensor(float(alpha))
    return sd


def _host(layer):
    host = nn.Module()
    host.proj = layer
    return host


def _applied(make_layer, loras):
    host = _host(make_layer(False))
    patched, unmatched = apply_loras(host, loras)
    assert patched == 1 and unmatched == []
    return host.proj


def _weight_side_delta(deltas):
    return ops.apply_lora_deltas(torch.zeros(OUT, IN), deltas)


def _expected(base, x, deltas):
    return base.float() + F.linear(x.float().reshape(-1, IN), _weight_side_delta(deltas)).reshape(base.shape)


def _close(got, want, dtype):
    got, want = got.float(), want.float()
    if dtype is torch.float32:
        assert torch.allclose(got, want, atol=1e-4, rtol=1e-4), (got - want).abs().max()
        return
    rel = ((got - want).norm() / want.norm()).item()
    assert rel < 1e-2, rel


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("alpha", [None, 4.0])
@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("path", list(PATHS))
def test_activation_side_lokr_matches_the_weight_side_kron(path, case, alpha, dtype):
    make_layer, run, label = PATHS[path]
    x = _input(dtype)
    base = run(make_layer(False), x)
    lin = _applied(make_layer, [(_lokr_sd(case, alpha), 1.7)])
    assert lin.lora_deltas[0].kron

    got = run(lin, x)
    want = _expected(base, x, lin.lora_deltas)

    assert not torch.allclose(base.float(), want.float(), atol=0.1)
    _close(got, want, dtype)
    _close(run(lin, x), want, dtype)
    assert set(ops._lora_path_layers) == {label}


@pytest.mark.parametrize("case", list(CASES))
def test_the_cheaper_contraction_order_and_factor_form_are_chosen(case):
    lin = _applied(_fp8_layer, [(_lokr_sd(case, 4.0), 1.0)])
    d = lin.lora_deltas[0]
    branch = ops._build_lokr_branch(d, torch.float32, torch.device("cpu"))
    for chain, spec in ((branch.chain1, CASES[case]["w1"]), (branch.chain2, CASES[case]["w2"])):
        assert len(chain) == (1 if len(spec) == 2 else 2)
    c1 = sum(m.numel() for m in branch.chain1)
    c2 = sum(m.numel() for m in branch.chain2)
    (o1, i1), (o2, i2) = d.up.shape, d.down.shape
    w1_first_cost = i2 * c1 + o1 * c2
    w2_first_cost = i1 * c2 + o2 * c1
    assert branch.w1_first == (w1_first_cost <= w2_first_cost)


def test_both_contraction_orders_occur_across_the_cases():
    orders = set()
    for case in CASES:
        d = _applied(_fp8_layer, [(_lokr_sd(case), 1.0)]).lora_deltas[0]
        orders.add(ops._build_lokr_branch(d, torch.float32, torch.device("cpu")).w1_first)
    assert orders == {True, False}


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("w1_first", [True, False])
def test_either_order_is_exact(case, w1_first):
    d = _applied(_fp8_layer, [(_lokr_sd(case, 4.0), 0.6)]).lora_deltas[0]
    branch = ops._build_lokr_branch(d, torch.float32, torch.device("cpu"))
    branch.w1_first = w1_first
    x = _input(torch.float32).reshape(-1, IN)
    out = torch.zeros(x.shape[0], OUT)
    ops._add_lokr_branch(out, x, branch)
    _close(out, F.linear(x, _weight_side_delta([d])), torch.float32)


def test_row_chunking_keeps_the_result(monkeypatch):
    d = _applied(_fp8_layer, [(_lokr_sd("lowrank_both", 4.0), 1.3)]).lora_deltas[0]
    branch = ops._build_lokr_branch(d, torch.float32, torch.device("cpu"))
    x = _input(torch.float32).reshape(-1, IN)
    whole = torch.zeros(x.shape[0], OUT)
    ops._add_lokr_branch(whole, x, branch)
    monkeypatch.setattr(ops, "_NVFP4_LORA_BRANCH_CHUNK_BYTES", branch.width * 4 * 5)
    chunked = torch.zeros(x.shape[0], OUT)
    ops._add_lokr_branch(chunked, x, branch)
    _close(chunked, whole, torch.float32)


def _plain_kohya(rank, seed):
    g = torch.Generator().manual_seed(seed)
    return {
        "proj.lora_up.weight": torch.randn(OUT, rank, generator=g) * 0.3,
        "proj.lora_down.weight": torch.randn(rank, IN, generator=g) * 0.3,
        "proj.alpha": torch.tensor(rank / 2.0),
    }


def _spy(monkeypatch, name):
    calls = []
    real = getattr(ops, name)

    def spy(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(ops, name, spy)
    return calls


def _weight_side_spy(monkeypatch):
    calls = []
    real = ops.apply_lora_deltas

    def spy(weight, deltas):
        if deltas:
            calls.append(len(deltas))
        return real(weight, deltas)

    monkeypatch.setattr(ops, "apply_lora_deltas", spy)
    return calls


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("path", list(PATHS))
def test_lokr_mixed_with_plain_loras_stays_on_the_activation(monkeypatch, path, dtype):
    make_layer, run, label = PATHS[path]
    weight_side = _weight_side_spy(monkeypatch)
    x = _input(dtype)
    base = run(make_layer(False), x)
    lin = _applied(make_layer, [
        (_plain_kohya(4, 1), 0.8),
        (_lokr_sd("lowrank_w2", 4.0), 1.2),
        (_plain_kohya(8, 2), 0.5),
        (_lokr_sd("full_w2_first", seed=33), 0.9),
    ])

    got = run(lin, x)
    assert weight_side == []
    _close(got, _expected(base, x, lin.lora_deltas), dtype)
    assert set(ops._lora_path_layers) == {label}
    fused = lin.__dict__[ops._LORA_FUSED_ATTR][2]
    assert sum(isinstance(e, ops._LokrBranch) for e in fused) == 2
    assert sum(not isinstance(e, ops._LokrBranch) for e in fused) == 1


def test_zero_strength_lokr_contributes_nothing_and_builds_no_branch():
    x = _input(torch.bfloat16)
    base = _fp8_fast(_fp8_layer(False), x)
    lin = _applied(_fp8_layer, [(_lokr_sd("lowrank_both", 4.0), 1.0)])
    lin.lora_deltas[0].scale = 0.0
    assert torch.equal(_fp8_fast(lin, x), base)
    assert lin.__dict__[ops._LORA_FUSED_ATTR][2] == []


def test_factors_are_prepared_once_and_rebuilt_on_strength_change(monkeypatch):
    builds = _spy(monkeypatch, "_build_lokr_branch")
    x = _input(torch.bfloat16)
    base = _fp8_fast(_fp8_layer(False), x)
    lin = _applied(_fp8_layer, [(_lokr_sd("lowrank_both", 4.0), 1.0)])
    for _ in range(3):
        _fp8_fast(lin, x)
        _dequant(lin, x)
    assert len(builds) == 1
    lin.lora_deltas[0].scale = 0.4
    got = _fp8_fast(lin, x)
    assert len(builds) == 2
    _close(got, _expected(base, x, lin.lora_deltas), torch.bfloat16)


def test_remove_invalidates_the_prepared_factors():
    host = _host(_fp8_layer(False))
    apply_loras(host, [(_lokr_sd("full_w1_first"), 1.0)])
    _fp8_fast(host.proj, _input(torch.bfloat16))
    assert ops._LORA_FUSED_ATTR in host.proj.__dict__
    remove_loras(host)
    assert ops._LORA_FUSED_ATTR not in host.proj.__dict__
    assert not host.proj.lora_deltas


def test_fast_path_no_longer_rejects_lokr():
    lin = _applied(_fp8_layer, [(_lokr_sd("lowrank_both", 4.0), 1.0), (_plain_kohya(4, 3), 1.0)])
    kwargs = dict(input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
                  in_features=IN, out_features=OUT)
    assert ops._deltas_output_branch_ok(lin.lora_deltas, OUT) is True
    assert ops._scaled_mm_fast_path_reject_reason(
        weight_dtype=torch.float8_e4m3fn, has_weight_scale=True, lora_deltas=lin.lora_deltas, **kwargs) is None
    assert ops._nvfp4_fast_path_reject_reason(lora_deltas=lin.lora_deltas, **kwargs) is None


def test_a_lokr_that_does_not_tile_the_output_stays_weight_side_for_that_delta_only():
    bad = LoraDelta(down=torch.randn(5, 8) * 0.2, up=torch.randn(16, 8) * 0.2, alpha=1.0, scale=1.0, kron=True)
    plain = _delta(4, 1.0, 1, torch.float32)
    good = _applied(_fp8_layer, [(_lokr_sd("full_w1_first"), 1.0)]).lora_deltas[0]
    assert ops._deltas_output_branch_ok([bad], OUT) is False
    output_side, weight_side = ops.partition_output_branch_deltas([plain, bad, good], OUT)
    assert output_side == [plain, good]
    assert weight_side == [bad]


def test_kill_switch_restores_the_weight_side_lokr(monkeypatch):
    monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
    weight_side = _weight_side_spy(monkeypatch)
    builds = _spy(monkeypatch, "_build_lokr_branch")
    lin = _applied(_fp8_layer, [(_lokr_sd("lowrank_both", 4.0), 1.0), (_plain_kohya(4, 3), 1.0)])
    kwargs = dict(input_dtype=torch.bfloat16, input_is_cuda=True, weight_is_cuda=True,
                  in_features=IN, out_features=OUT)
    assert ops._deltas_output_branch_ok(lin.lora_deltas, OUT) is False
    assert ops._nvfp4_fast_path_reject_reason(lora_deltas=lin.lora_deltas, **kwargs) == "lora_deltas"
    x = _input(torch.bfloat16)
    got = _dequant(lin, x)
    assert weight_side == [1]
    assert builds == []
    assert set(ops._lora_path_layers) == {"weight-side (lokr)"}
    _close(got, _expected(_dequant(_fp8_layer(False), x), x, lin.lora_deltas), torch.bfloat16)


def test_grad_enabled_branch_matches(monkeypatch):
    lin = _applied(_fp8_layer, [(_lokr_sd("lowrank_w1", 4.0), 1.0), (_plain_kohya(4, 3), 0.7)])
    x = _input(torch.float32)
    with torch.no_grad():
        base = _fp8_layer(False).forward_comfy_cast_weights(x)
    got = lin.forward_comfy_cast_weights(x)
    _close(got.detach(), _expected(base, x, lin.lora_deltas), torch.float32)


def _fp8_krea2():
    m = Krea2.from_config(TINY, ops.pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    sd = {k: torch.randn_like(v.float()) * 0.02 if v.is_floating_point() else v.clone()
          for k, v in m.state_dict().items()}
    load_into_module(m, sd, match_model_spec(TINY))
    wq = m.blocks[0].attn.wq
    wq.weight.data = (torch.randn_like(wq.weight.data.float()) * 0.05).to(torch.float8_e4m3fn)
    return m, wq


def _krea2_lokr(seed):
    g = torch.Generator().manual_seed(seed)
    return {
        "blocks.0.attn.wq.lokr_w1": torch.randn(4, 4, generator=g) * 0.3,
        "blocks.0.attn.wq.lokr_w2_a": torch.randn(8, 2, generator=g) * 0.3,
        "blocks.0.attn.wq.lokr_w2_b": torch.randn(2, 8, generator=g) * 0.3,
        "blocks.0.attn.wq.alpha": torch.tensor(4.0),
    }


def test_window_edges_invalidate_and_an_out_of_window_lokr_contributes_nothing(monkeypatch):
    m, wq = _fp8_krea2()
    x = torch.randn(1, 6, wq.in_features, generator=torch.Generator().manual_seed(9)).to(torch.bfloat16)
    with torch.no_grad():
        bare = wq(x)
    hook = LoraStepWindowHook(NativeModel("diffusion_model", m),
                              [(_krea2_lokr(5), 0.8, LoraStepWindow(2, 3))])
    builds = _spy(monkeypatch, "_build_lokr_branch")

    def step():
        with torch.no_grad():
            got = wq(x)
            if wq.lora_deltas:
                monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
                want = wq(x)
                monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV)
                _close(got, want, torch.bfloat16)
        return got

    hook.on_start(6)
    assert torch.equal(step(), bare)
    hook.on_step(0, 6, None, 0.0, None)
    assert ops._LORA_FUSED_ATTR not in wq.__dict__
    inside = step()
    hook.on_step(1, 6, None, 0.0, None)
    step()
    hook.on_step(2, 6, None, 0.0, None)
    assert ops._LORA_FUSED_ATTR not in wq.__dict__
    assert torch.equal(step(), bare)
    hook.close(final=True)

    assert len(builds) == 1
    assert not torch.allclose(inside.float(), bare.float(), atol=1e-2)
