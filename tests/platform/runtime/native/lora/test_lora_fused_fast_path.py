from __future__ import annotations

import logging
import types
from unittest.mock import patch

import pytest
import torch

from src.platform.runtime.native.engine import NativeGenerator, NativeModel
from src.platform.runtime.native.lora import LoraStepWindow, LoraStepWindowHook
from src.platform.runtime.native.lora.apply import apply_loras, remove_loras
from src.platform.runtime.native.lora.key_mapping import LoraDelta
from vendor.gpl.comfyui import ops

from tests.platform.runtime.native._nvfp4_ref import F4_MAX, F8_MAX, quantize_nvfp4
from tests.platform.runtime.native.lora.test_lora import _build, _kohya_lora

IN, OUT, ROWS = 64, 96, 24
TOL = {torch.bfloat16: 3e-2, torch.float32: 1e-5}

STACKS = {
    "one": [(8, 1.0, None)],
    "two": [(4, 1.0, None), (16, 0.6, None)],
    "three_with_zero": [(4, 0.8, None), (8, 0.0, None), (2, 1.3, None)],
    "equal_strengths": [(8, 0.5, None), (8, 0.5, None)],
    "sliced": [(4, 1.0, (0, 16, 32)), (8, 0.7, None), (2, 0.5, (0, 16, 32))],
}


@pytest.fixture(autouse=True)
def _fused_default(monkeypatch):
    monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV, raising=False)
    ops._lora_path_layers.clear()
    yield
    ops._lora_path_layers.clear()


def _delta(rank, strength, seed, dtype, target_slice=None):
    g = torch.Generator().manual_seed(seed)
    width = target_slice[2] if target_slice else OUT
    return LoraDelta(
        down=(torch.randn(rank, IN, generator=g) * 0.3).to(dtype),
        up=(torch.randn(width, rank, generator=g) * 0.3).to(dtype),
        alpha=rank / 2.0, scale=strength, target_slice=target_slice,
    )


def _stack(name, dtype):
    return [_delta(r, s, i + 1, dtype, ts) for i, (r, s, ts) in enumerate(STACKS[name])]


def _lokr(dtype):
    g = torch.Generator().manual_seed(99)
    return LoraDelta(down=(torch.randn(12, 8, generator=g) * 0.2).to(dtype),
                     up=(torch.randn(8, 8, generator=g) * 0.2).to(dtype),
                     alpha=1.0, scale=0.9, kron=True)


def _clone(deltas):
    return [LoraDelta(down=d.down.clone(), up=d.up.clone(), alpha=d.alpha, scale=d.scale,
                      target_slice=d.target_slice, kron=d.kron) for d in deltas]


def _input(dtype, seed=3):
    g = torch.Generator().manual_seed(seed)
    return torch.randn(2, ROWS // 2, IN, generator=g).to(dtype)


def _fp8_layer(bias):
    lin = ops.fp8_ops.Linear(IN, OUT, bias=bias)
    g = torch.Generator().manual_seed(7)
    sd = {"weight": (torch.randn(OUT, IN, generator=g) * 0.05).to(torch.float8_e4m3fn),
          "weight_scale": torch.tensor(1.0)}
    if bias:
        sd["bias"] = torch.randn(OUT, generator=g)
    lin.load_state_dict(sd, strict=False, assign=True)
    return lin


def _nvfp4_layer(bias):
    lin = ops.fp8_ops.Linear(IN, OUT, bias=bias)
    g = torch.Generator().manual_seed(11)
    w = torch.randn(OUT, IN, generator=g) * 0.05
    pts = (w.abs().amax() / (F4_MAX * F8_MAX)).clamp(min=1e-8)
    packed, block_sw, _, _ = quantize_nvfp4(w, pts)
    sd = {"weight": packed, "weight_scale": block_sw, "weight_scale_2": pts.clone()}
    if bias:
        sd["bias"] = torch.randn(OUT, generator=g)
    lin._load_from_state_dict(sd, "", {}, True, [], [], [])
    return lin


def _fake_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
    out = (a.to(torch.float32) * scale_a) @ (b.to(torch.float32) * scale_b)
    if bias is not None:
        out = out + bias.to(torch.float32)
    return out.to(out_dtype)


def _fp8_fast(lin, x):
    with patch("torch._scaled_mm", _fake_scaled_mm), torch.no_grad():
        out = lin._forward_scaled_mm(x)
    assert out is not None
    return out


def _nvfp4_fast(lin, x):
    base = torch.randn(x.reshape(-1, IN).shape[0], OUT, generator=torch.Generator().manual_seed(5))

    def fake(*_a, **kwargs):
        return base.to(kwargs["output_dtype"])

    with patch("torch.nn.functional.scaled_mm", side_effect=fake), torch.no_grad():
        out = lin._forward_nvfp4_scaled_mm(x)
    assert out is not None
    return out


def _dequant(lin, x):
    with torch.no_grad():
        return lin.forward_comfy_cast_weights(x)


def _old_and_new(monkeypatch, make_layer, run, deltas, x):
    old = make_layer()
    old.lora_deltas = _clone(deltas)
    monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
    want = run(old, x)
    monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV)
    bare = make_layer()
    base = run(bare, x)
    new = make_layer()
    new.lora_deltas = deltas
    return base, want, run(new, x), run(new, x)


def _assert_close(got, want, dtype):
    tol = TOL[dtype]
    assert torch.allclose(got.float(), want.float(), atol=tol, rtol=tol), (got.float() - want.float()).abs().max()


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("stack", list(STACKS))
def test_fp8_fast_path_fused_matches_old_unfused(monkeypatch, dtype, bias, stack):
    x = _input(dtype)
    base, want, got, again = _old_and_new(
        monkeypatch, lambda: _fp8_layer(bias), _fp8_fast, _stack(stack, dtype), x)
    assert not torch.allclose(base.float(), want.float(), atol=0.1)
    _assert_close(got, want, dtype)
    _assert_close(again, want, dtype)


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("stack", list(STACKS))
def test_nvfp4_fast_path_fused_matches_old_unfused(monkeypatch, dtype, bias, stack):
    x = _input(dtype)
    base, want, got, again = _old_and_new(
        monkeypatch, lambda: _nvfp4_layer(bias), _nvfp4_fast, _stack(stack, dtype), x)
    assert not torch.allclose(base.float(), want.float(), atol=0.1)
    _assert_close(got, want, dtype)
    _assert_close(again, want, dtype)


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float32])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("with_lokr", [False, True])
def test_dequant_path_cached_matches_old(monkeypatch, dtype, bias, with_lokr):
    deltas = _stack("sliced", dtype) + ([_lokr(dtype)] if with_lokr else [])
    x = _input(dtype)
    base, want, got, again = _old_and_new(monkeypatch, lambda: _fp8_layer(bias), _dequant, deltas, x)
    assert not torch.allclose(base.float(), want.float(), atol=0.1)
    _assert_close(got, want, dtype)
    _assert_close(again, want, dtype)


def test_factors_in_another_dtype_are_cast_once_and_still_match(monkeypatch):
    deltas = _stack("two", torch.float32)
    x = _input(torch.bfloat16)
    _base, want, got, _again = _old_and_new(monkeypatch, lambda: _fp8_layer(True), _fp8_fast, deltas, x)
    _assert_close(got, want, torch.bfloat16)
    assert all(d.down.dtype is torch.bfloat16 and d.up.dtype is torch.bfloat16 for d in deltas)


def test_multi_adapter_factors_become_views_of_one_fused_buffer():
    lin = _fp8_layer(False)
    lin.lora_deltas = _stack("two", torch.bfloat16)
    _fp8_fast(lin, _input(torch.bfloat16))
    (entry,) = lin.__dict__[ops._LORA_FUSED_ATTR][2]
    down_cat, up_cat_t = entry[1], entry[2]
    for d in lin.lora_deltas:
        assert d.down.untyped_storage().data_ptr() == down_cat.untyped_storage().data_ptr()
        assert d.up.untyped_storage().data_ptr() == up_cat_t.untyped_storage().data_ptr()


def test_all_zero_strength_contributes_nothing_and_builds_no_branch():
    x = _input(torch.bfloat16)
    base = _fp8_fast(_fp8_layer(True), x)
    lin = _fp8_layer(True)
    lin.lora_deltas = [_delta(4, 0.0, 1, torch.bfloat16), _delta(8, 0.0, 2, torch.bfloat16)]
    assert torch.equal(_fp8_fast(lin, x), base)
    assert lin.__dict__[ops._LORA_FUSED_ATTR][2] == []


def _spy(monkeypatch, name):
    calls = []
    real = getattr(ops, name)

    def spy(*a, **k):
        calls.append(k.get("owner"))
        return real(*a, **k)

    monkeypatch.setattr(ops, name, spy)
    return calls


@pytest.mark.parametrize("make_layer,run", [(_fp8_layer, _fp8_fast), (_nvfp4_layer, _nvfp4_fast)])
def test_fast_path_calls_the_shared_fused_helper(monkeypatch, make_layer, run):
    fused = _spy(monkeypatch, "_add_lora_output_branch")
    unfused = _spy(monkeypatch, "_lora_output_branch")
    lin = make_layer(False)
    lin.lora_deltas = _stack("three_with_zero", torch.bfloat16)
    run(lin, _input(torch.bfloat16))
    assert fused == [lin]
    assert unfused == []


@pytest.mark.parametrize("make_layer,run", [(_fp8_layer, _fp8_fast), (_nvfp4_layer, _nvfp4_fast)])
def test_kill_switch_restores_the_unfused_fast_path(monkeypatch, make_layer, run):
    monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
    fused = _spy(monkeypatch, "_add_lora_output_branch")
    unfused = _spy(monkeypatch, "_lora_output_branch")
    builds = _spy(monkeypatch, "_build_lora_fused")
    lin = make_layer(False)
    lin.lora_deltas = _stack("two", torch.bfloat16)
    run(lin, _input(torch.bfloat16))
    assert fused == []
    assert len(unfused) == 1
    assert builds == []
    assert ops._LORA_FUSED_ATTR not in lin.__dict__


def test_kill_switch_restores_the_per_forward_fuse_on_the_dequant_path(monkeypatch):
    monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
    per_forward = _spy(monkeypatch, "_fuse_output_branch_deltas")
    builds = _spy(monkeypatch, "_build_lora_fused")
    lin = _fp8_layer(False)
    lin.lora_deltas = _stack("two", torch.bfloat16)
    _dequant(lin, _input(torch.bfloat16))
    _dequant(lin, _input(torch.bfloat16))
    assert len(per_forward) == 2
    assert builds == []


def test_fused_factors_are_built_once_across_forwards(monkeypatch):
    builds = _spy(monkeypatch, "_build_lora_fused")
    lin = _fp8_layer(True)
    lin.lora_deltas = _stack("three_with_zero", torch.bfloat16)
    for _ in range(4):
        _fp8_fast(lin, _input(torch.bfloat16))
        _dequant(lin, _input(torch.bfloat16))
    assert len(builds) == 1


def _reference(lin_factory, deltas, x, monkeypatch, run):
    ref = lin_factory()
    ref.lora_deltas = _clone(deltas)
    monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
    out = run(ref, x)
    monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV)
    return out


def test_strength_change_rebuilds_and_matches(monkeypatch):
    builds = _spy(monkeypatch, "_build_lora_fused")
    x = _input(torch.bfloat16)
    lin = _fp8_layer(False)
    lin.lora_deltas = _stack("two", torch.bfloat16)
    _fp8_fast(lin, x)
    lin.lora_deltas[1].scale = 1.7
    got = _fp8_fast(lin, x)
    assert len(builds) == 2
    _assert_close(got, _reference(lambda: _fp8_layer(False), lin.lora_deltas, x, monkeypatch, _fp8_fast),
                  torch.bfloat16)


def test_set_change_rebuilds_and_matches(monkeypatch):
    builds = _spy(monkeypatch, "_build_lora_fused")
    x = _input(torch.bfloat16)
    lin = _fp8_layer(False)
    lin.lora_deltas = _stack("two", torch.bfloat16)
    _fp8_fast(lin, x)
    lin.lora_deltas.append(_delta(6, 0.9, 42, torch.bfloat16))
    grown = _fp8_fast(lin, x)
    _assert_close(grown, _reference(lambda: _fp8_layer(False), lin.lora_deltas, x, monkeypatch, _fp8_fast),
                  torch.bfloat16)
    lin.lora_deltas = lin.lora_deltas[:1]
    shrunk = _fp8_fast(lin, x)
    _assert_close(shrunk, _reference(lambda: _fp8_layer(False), lin.lora_deltas, x, monkeypatch, _fp8_fast),
                  torch.bfloat16)
    assert len(builds) == 3


def _fp8_flux(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    m = _build(ops.pick_operations(torch.float8_e4m3fn, torch.bfloat16))
    qkv = m.double_blocks[0].img_attn.qkv
    qkv.weight.data = qkv.weight.data.to(torch.float8_e4m3fn)
    return m, qkv


def test_window_edges_invalidate_and_out_of_window_adapter_contributes_nothing(monkeypatch):
    m, qkv = _fp8_flux(monkeypatch)
    apply_loras(m, [(_kohya_lora(seed=1), 1.0)])
    hook = LoraStepWindowHook(NativeModel("diffusion_model", m),
                              [(_kohya_lora(seed=5), 0.8, LoraStepWindow(2, 3))])
    builds = _spy(monkeypatch, "_build_lora_fused")
    x = torch.randn(1, 6, qkv.in_features, generator=torch.Generator().manual_seed(9)).to(torch.bfloat16)

    def step(expect_len):
        assert len(qkv.lora_deltas) == expect_len
        assert ops._LORA_FUSED_ATTR not in qkv.__dict__ or expect_len == len(qkv.__dict__[ops._LORA_FUSED_ATTR][1])
        with torch.no_grad():
            got = qkv(x)
            monkeypatch.setenv(ops.NATIVE_LORA_FUSED_ENV, "off")
            want = qkv(x)
            monkeypatch.delenv(ops.NATIVE_LORA_FUSED_ENV)
        _assert_close(got, want, torch.bfloat16)
        return got

    hook.on_start(6)
    outside = step(1)
    hook.on_step(0, 6, None, 0.0, None)
    assert ops._LORA_FUSED_ATTR not in qkv.__dict__
    inside = step(2)
    hook.on_step(1, 6, None, 0.0, None)
    step(2)
    hook.on_step(2, 6, None, 0.0, None)
    assert ops._LORA_FUSED_ATTR not in qkv.__dict__
    after = step(1)
    hook.close(final=True)

    assert len(builds) == 3
    assert not torch.allclose(inside.float(), outside.float(), atol=1e-2)
    _assert_close(after, outside, torch.bfloat16)

    remove_loras(m)
    assert ops._LORA_FUSED_ATTR not in qkv.__dict__


def test_path_summary_is_logged_once_per_run(caplog):
    fast = _fp8_layer(False)
    fast.lora_deltas = _stack("two", torch.bfloat16)
    lokr = _fp8_layer(False)
    lokr.lora_deltas = _stack("one", torch.bfloat16) + [_lokr(torch.bfloat16)]
    plain = _fp8_layer(False)
    plain.lora_deltas = _stack("one", torch.bfloat16)
    for _ in range(3):
        _fp8_fast(fast, _input(torch.bfloat16))
        _dequant(lokr, _input(torch.bfloat16))
        _dequant(plain, _input(torch.bfloat16))

    caplog.set_level(logging.INFO, logger=ops.logger.name)
    ops.log_lora_path_summary()
    ops.log_lora_path_summary()

    lines = [r.getMessage() for r in caplog.records if "lora forward paths" in r.getMessage()]
    assert lines == ["lora forward paths (layers): dequant (fp8 matmul off) 1, fused-fast 1, weight-side (lokr) 1"]


def test_sampling_run_scope_emits_the_path_summary(monkeypatch):
    calls = []
    monkeypatch.setattr("src.platform.runtime.native.engine.log_lora_path_summary", lambda: calls.append(1))
    fake = types.SimpleNamespace(dit=types.SimpleNamespace(module=types.SimpleNamespace(), effective_revision=0))
    with NativeGenerator._run_cache(fake):
        assert calls == []
    assert calls == [1]
