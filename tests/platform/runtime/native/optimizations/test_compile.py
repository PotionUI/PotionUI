from __future__ import annotations

import sys
import types
from contextlib import ExitStack

import torch
import torch.nn as nn
import pytest

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.depth_decoder import DepthDecoderModule
from src.platform.runtime.native.io.quant_formats import build_convrot_hadamard
from src.platform.runtime.native.kernels import fp8_ops, Fp8ScaledLinear, disable_weight_init
from src.platform.runtime.native.optimizations import compile as tc
from src.platform.settings import runtime_flags
from tests.fixtures.kernel_gemm import installed_gemm_verdicts


# --- helpers ------------------------------------------------------------------


_FLUX = {
    "image_model": "flux",
    "in_channels": 16,
    "out_channels": 16,
    "hidden_size": 128,
    "context_in_dim": 32,
    "num_heads": 1,
    "depth": 2,                 # ≥2 so the block lists are "repeated"
    "depth_single_blocks": 2,
    "axes_dim": [16, 56, 56],
    "mlp_ratio": 4.0,
    "theta": 10000,
    "patch_size": 2,
    "qkv_bias": True,
    "guidance_embed": True,
}


class _Blocks(nn.Module):
    """Controllable DiT-shaped toy: a homogeneous ``blocks`` ModuleList."""

    def __init__(self, n: int = 3, dim: int = 8) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(disable_weight_init.Linear(dim, dim) for _ in range(n))
        self.norm = disable_weight_init.RMSNorm(dim)
        for p in self.parameters():
            nn.init.normal_(p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for blk in self.blocks:
            x = blk(x)
        return self.norm(x)


class _MockNM:
    def __init__(self, module: nn.Module) -> None:
        self.module = module
        self._compiled = None


def _enable(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", True)
    monkeypatch.setattr(tc, "_triton_available", lambda: True)


@pytest.fixture(autouse=True)
def _gemm_flags_off(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_fp8_matmul", False)
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_nvfp4_matmul", False)


@pytest.fixture
def gemm_machine(monkeypatch):
    with ExitStack() as stack:

        def configure(*, flag: bool, supported: bool, nvfp4_flag: bool | None = None) -> None:
            monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_fp8_matmul", flag)
            monkeypatch.setitem(
                runtime_flags.runtime_flag_values(),
                "native_nvfp4_matmul",
                flag if nvfp4_flag is None else nvfp4_flag,
            )
            stack.enter_context(installed_gemm_verdicts(fp8=supported, nvfp4=supported))

        yield configure


def _fp8_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = Fp8ScaledLinear(dim, dim)
    layer.weight = nn.Parameter(torch.randn(dim, dim).to(torch.float8_e4m3fn), requires_grad=False)
    layer.weight_scale = torch.tensor(0.5)
    layer.bias = nn.Parameter(torch.randn(dim), requires_grad=False)
    return layer


def _fp8_pre_scaled_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.pre_quant_scale = torch.rand(dim) + 0.5
    return layer


def _int8_convrot_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.weight = nn.Parameter(torch.randint(-5, 5, (dim, dim), dtype=torch.int8), requires_grad=False)
    layer.convrot_hadamard = build_convrot_hadamard(dim, device="cpu", dtype=torch.float32).clone()
    layer.convrot_groupsize = dim
    return layer


def _fp8_e5m2_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.weight = nn.Parameter(torch.randn(dim, dim).to(torch.float8_e5m2), requires_grad=False)
    return layer

def _fp8_misaligned_linear() -> Fp8ScaledLinear:
    return _fp8_linear(24)

def _fp8_per_channel_scale_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.weight_scale = torch.full((dim, 1), 0.5)
    return layer

def _fp8_per_channel_input_scale_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.input_scale = torch.full((2,), 0.5)
    return layer

def _int8_linear(dim: int = 16) -> Fp8ScaledLinear:
    layer = _fp8_linear(dim)
    layer.weight = nn.Parameter(torch.randint(-5, 5, (dim, dim), dtype=torch.int8), requires_grad=False)
    return layer

def _nvfp4_linear() -> Fp8ScaledLinear:
    layer = fp8_ops.Linear(32, 16)
    layer._is_nvfp4 = True
    return layer

class _QuantBlock(nn.Module):
    def __init__(self, make_linear=_fp8_linear) -> None:
        super().__init__()
        self.a = make_linear()
        self.b = make_linear()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.b(torch.nn.functional.gelu(self.a(x)))


class _QuantDit(nn.Module):
    def __init__(self, make_linear=_fp8_linear, n: int = 2) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(_QuantBlock(make_linear) for _ in range(n))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for block in self.blocks:
            x = block(x)
        return x


class _StreamedBlock(nn.Module):
    def __init__(self, dim: int = 16) -> None:
        super().__init__()
        self.a = disable_weight_init.Linear(dim, dim)
        self.b = disable_weight_init.Linear(dim, dim)
        for p in self.parameters():
            nn.init.normal_(p)
        for leaf in (self.a, self.b):
            leaf.comfy_cast_weights = True
            leaf.stream_non_blocking = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.b(torch.nn.functional.gelu(self.a(x)))


class _StreamedDit(nn.Module):
    def __init__(self, n: int = 2) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(_StreamedBlock() for _ in range(n))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for block in self.blocks:
            x = block(x)
        return x


def _streaming_nm(module: nn.Module, active: bool = True) -> _MockNM:
    nm = _MockNM(module)
    nm.is_streaming = active
    return nm


def _eager_backend_compile(monkeypatch):
    real_compile = torch.compile

    def eager_compile(target, **kwargs):
        return real_compile(target, backend="eager", dynamic=kwargs["dynamic"])

    monkeypatch.setattr(torch, "compile", eager_compile)


def _record_compile_calls(monkeypatch):
    calls = []

    def fake_compile(module, **kwargs):
        calls.append(kwargs)
        return _FakeCompiled(module)

    monkeypatch.setattr(torch, "compile", fake_compile)
    return calls


def test_torch_compile_follows_the_runtime_flag(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", False)
    assert tc.torch_compile_enabled() is False
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", True)
    assert tc.torch_compile_enabled() is True


def test_torch_compile_is_off_by_default():
    assert runtime_flags.RUNTIME_FLAG_BY_KEY["native_torch_compile"].default is False


# --- block discovery on real Flux ---------------------------------------------


def test_find_block_lists_on_real_flux():
    flux = Flux.from_config(_FLUX, disable_weight_init)
    lists = tc.find_block_lists(flux)
    # Flux exposes double_blocks + single_blocks as direct homogeneous children.
    assert len(lists) == 2
    assert {len(x) for x in lists} == {2}
    for module_list in lists:
        assert len({type(m) for m in module_list}) == 1


def test_find_block_lists_ignores_singletons_and_heterogeneous():
    class _Mixed(nn.Module):
        def __init__(self):
            super().__init__()
            self.solo = nn.ModuleList([nn.Linear(4, 4)])                  # len 1 -> ignored
            self.mixed = nn.ModuleList([nn.Linear(4, 4), nn.RMSNorm(4)])  # heterogeneous -> ignored

    assert tc.find_block_lists(_Mixed()) == []


def _case_disabled(mp):
    mp.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", False)
    return _MockNM(_Blocks()), True, True


def _case_cpu(mp):
    return _MockNM(_Blocks()), True, False


def _case_not_resident(mp):
    return _MockNM(_Blocks()), False, True


def _case_streamer_inactive(mp):
    return _streaming_nm(_Blocks(), active=False), False, True


def _case_streamed_ok(mp):
    return _streaming_nm(_StreamedDit()), False, True


def _case_streamed_leaf_hooks(mp):
    dit = _StreamedDit()
    dit.blocks[0].a.register_forward_pre_hook(lambda m, a: None)
    return _streaming_nm(dit), False, True


def _case_streamed_block_hook(mp):
    dit = _StreamedDit()
    dit.blocks[0].register_forward_hook(lambda m, a, o: None)
    return _streaming_nm(dit), False, True


def _case_resident_leaf_hooks(mp):
    dit = _StreamedDit()
    dit.blocks[0].a.register_forward_pre_hook(lambda m, a: None)
    return _MockNM(dit), True, True


def _case_no_triton(mp):
    mp.setattr(tc, "_triton_available", lambda: False)
    return _MockNM(_Blocks()), True, True


def _case_fp8(make_linear):
    return lambda mp: (_MockNM(_QuantDit(make_linear)), True, True)


def _case_fp8_streamed(mp):
    return _streaming_nm(_QuantDit()), False, True


def _case_fp8_lora(mp):
    dit = _QuantDit()
    dit.blocks[0].a.lora_deltas = [object()]
    return _MockNM(dit), True, True


def _case_fp8_masked_lora(mp):
    dit = _QuantDit()
    dit.blocks[0].a.lora_masked_deltas = [object()]
    return _MockNM(dit), True, True


def _case_no_torch_compile(mp):
    mp.delattr(torch, "compile")
    return _MockNM(_Blocks()), True, True


def _case_no_module(mp):
    return _MockNM(None), True, True


def _case_float_lora(mp):
    dit = _Blocks(n=2)
    dit.blocks[0].lora_deltas = [object()]
    return _MockNM(dit), True, True


def _case_no_blocks(mp):
    class _Flat(nn.Module):
        def __init__(self):
            super().__init__()
            self.lin = disable_weight_init.Linear(4, 4)

    return _MockNM(_Flat()), True, True


def _case_float_ok(mp):
    return _MockNM(_Blocks(n=3)), True, True


@pytest.mark.parametrize(
    "build, expected",
    [
        (_case_disabled, (False, "disabled")),
        (_case_cpu, (False, "cpu")),
        (_case_not_resident, (False, "not-resident")),
        (_case_streamer_inactive, (False, "not-resident")),
        (_case_streamed_ok, (True, "ok")),
        (_case_streamed_leaf_hooks, (False, "streaming-hooks")),
        (_case_streamed_block_hook, (True, "ok")),
        (_case_resident_leaf_hooks, (True, "ok")),
        (_case_no_triton, (False, "no-triton")),
        (_case_fp8(_fp8_pre_scaled_linear), (True, "ok")),
        (_case_fp8_streamed, (True, "ok")),
        (_case_fp8_lora, (False, "runtime-lora")),
        (_case_fp8_masked_lora, (False, "runtime-lora")),
        (_case_no_torch_compile, (False, "torch-no-compile")),
        (_case_no_module, (False, "no-module")),
        (_case_float_lora, (False, "runtime-lora")),
        (_case_no_blocks, (False, "no-block-lists")),
        (_case_float_ok, (True, "ok")),
    ],
    ids=lambda v: getattr(v, "__name__", None) if callable(v) else None,
)
def test_compile_gate_matrix(monkeypatch, build, expected):
    _enable(monkeypatch)
    nm, resident, is_cuda = build(monkeypatch)
    assert tc.compile_gate(nm, resident=resident, is_cuda=is_cuda) == expected


FP8_GEMM_LAYERS = [
    pytest.param(_fp8_linear, (False, "fp8-scaled-mm"), id="fp8-e4m3"),
    pytest.param(_fp8_e5m2_linear, (True, "ok"), id="fp8-e5m2"),
    pytest.param(_fp8_misaligned_linear, (True, "ok"), id="fp8-misaligned"),
    pytest.param(_fp8_per_channel_scale_linear, (True, "ok"), id="fp8-per-channel-weight-scale"),
    pytest.param(_fp8_per_channel_input_scale_linear, (True, "ok"), id="fp8-per-channel-input-scale"),
    pytest.param(_int8_linear, (True, "ok"), id="int8"),
    pytest.param(_int8_convrot_linear, (True, "ok"), id="int8-convrot"),
]


@pytest.mark.parametrize("make_linear, expected", FP8_GEMM_LAYERS)
def test_only_a_layer_the_fp8_gemm_takes_blocks_compile(monkeypatch, gemm_machine, make_linear, expected):
    _enable(monkeypatch)
    gemm_machine(flag=True, supported=True)
    nm = _MockNM(_QuantDit(make_linear))
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == expected


@pytest.mark.parametrize(
    "flag, supported, expected",
    [
        (True, True, (False, "fp8-scaled-mm")),
        (True, False, (True, "ok")),
        (False, True, (True, "ok")),
        (False, False, (True, "ok")),
    ],
    ids=["flag-on-supported", "flag-on-unsupported", "flag-off-supported", "flag-off-unsupported"],
)
def test_the_fp8_gemm_blocks_compile_only_with_its_flag_on_and_supporting_hardware(
    monkeypatch, gemm_machine, flag, supported, expected
):
    _enable(monkeypatch)
    gemm_machine(flag=flag, supported=supported)
    nm = _MockNM(_QuantDit())
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == expected


def test_the_gate_reads_the_fp8_flag_on_every_call(monkeypatch, gemm_machine):
    _enable(monkeypatch)
    nm = _MockNM(_QuantDit())
    gemm_machine(flag=False, supported=True)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (True, "ok")
    gemm_machine(flag=True, supported=True)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (False, "fp8-scaled-mm")
    gemm_machine(flag=False, supported=True)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (True, "ok")


@pytest.mark.parametrize("flag", [True, False], ids=["flag-on", "flag-off"])
@pytest.mark.parametrize("supported", [True, False], ids=["supported", "unsupported"])
def test_an_nvfp4_layer_blocks_compile_whatever_the_flag_and_hardware(monkeypatch, gemm_machine, flag, supported):
    _enable(monkeypatch)
    gemm_machine(flag=flag, supported=supported)
    dit = _QuantDit()
    dit.blocks[1].a = _nvfp4_linear()
    nm = _MockNM(dit)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (False, "nvfp4")


def test_one_layer_on_the_gemm_blocks_blocks_whose_other_layers_dequantize(monkeypatch, gemm_machine):
    _enable(monkeypatch)
    gemm_machine(flag=True, supported=True)
    dit = _QuantDit(_fp8_e5m2_linear)
    dit.blocks[1].b = _fp8_linear()
    nm = _MockNM(dit)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (False, "fp8-scaled-mm")


def test_a_gemm_layer_outside_the_compiled_blocks_does_not_block_compile(monkeypatch, gemm_machine):
    _enable(monkeypatch)
    gemm_machine(flag=True, supported=True)
    dit = _QuantDit(_fp8_e5m2_linear)
    dit.head = _fp8_linear()
    nm = _MockNM(dit)
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (True, "ok")


def test_a_linear_without_quantized_storage_does_not_block_compile(monkeypatch, gemm_machine):
    _enable(monkeypatch)
    gemm_machine(flag=True, supported=True)
    nm = _MockNM(_QuantDit(lambda: Fp8ScaledLinear(16, 16)))
    assert tc.compile_gate(nm, resident=True, is_cuda=True) == (True, "ok")


# --- maybe_compile_dit gating (no real compile) -------------------------------


def test_maybe_compile_noop_when_gate_fails(monkeypatch):
    _enable(monkeypatch)
    nm = _MockNM(_Blocks())
    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)
    assert nm._compiled is None
    for blk in nm.module.blocks:
        assert not tc.is_compiled(blk)


@pytest.mark.skipif(sys.platform == "win32", reason="torch.compile is unsupported on native Windows")
def test_real_eager_backend_compile_executes_and_restores(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    real_compile = torch.compile
    seen = []

    def eager_compile(module, **kwargs):
        seen.append(kwargs)
        return real_compile(module, backend="eager", dynamic=kwargs["dynamic"])

    monkeypatch.setattr(torch, "compile", eager_compile)
    torch.manual_seed(0)
    m = _Blocks(n=3, dim=8)
    x = torch.randn(2, 5, 8)
    reference = m(x)
    originals = list(m.blocks)

    nm = _MockNM(m)
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)

    assert nm._compiled is not None and nm._compiled.active
    assert all(isinstance(blk, torch._dynamo.eval_frame.OptimizedModule) for blk in m.blocks)
    assert [blk._orig_mod for blk in m.blocks] == originals
    assert all(tc.is_compiled(blk) for blk in m.blocks)
    assert torch.allclose(reference, m(x), atol=1e-5)

    handle = nm._compiled
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)
    assert nm._compiled is handle
    assert len(seen) == 3

    tc.restore_compiled(nm)
    assert nm._compiled is None
    assert list(m.blocks) == originals
    assert all(not tc.is_compiled(blk) for blk in m.blocks)
    assert torch.allclose(reference, m(x), atol=1e-5)
    assert seen == [{"mode": "default", "dynamic": True}] * 3
    torch._dynamo.reset()


@pytest.mark.skipif(sys.platform == "win32", reason="torch.compile is unsupported on native Windows")
@pytest.mark.parametrize(
    "make_block",
    [
        lambda: _QuantBlock(_fp8_linear),
        lambda: _QuantBlock(_fp8_pre_scaled_linear),
        lambda: _QuantBlock(_int8_convrot_linear),
        _StreamedBlock,
    ],
    ids=["fp8", "fp8-pre-scaled", "int8-convrot", "streamed"],
)
def test_block_traces_with_zero_graph_breaks(make_block):
    torch._dynamo.reset()
    torch.manual_seed(0)
    explanation = torch._dynamo.explain(make_block())(torch.randn(2, 5, 16))
    assert explanation.graph_break_count == 0
    torch._dynamo.reset()


@pytest.mark.skipif(sys.platform == "win32", reason="torch.compile is unsupported on native Windows")
def test_streamed_to_resident_flip_restores_forward_wraps_then_compiles_whole_blocks(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    _eager_backend_compile(monkeypatch)
    torch.manual_seed(0)
    dit = _StreamedDit(n=2)
    x = torch.randn(2, 5, 16)
    reference = dit(x)
    originals = list(dit.blocks)
    nm = _streaming_nm(dit)

    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)
    assert all("forward" in vars(block) for block in dit.blocks)

    tc.restore_compiled(nm)
    assert all("forward" not in vars(block) for block in dit.blocks)
    assert torch.equal(reference, dit(x))

    nm.is_streaming = False
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)
    assert nm._compiled.active and nm._compiled.forwards == []
    assert all(tc.is_compiled(block) for block in dit.blocks)
    assert [block._orig_mod for block in dit.blocks] == originals
    assert all("forward" not in vars(block._orig_mod) for block in dit.blocks)
    assert torch.allclose(reference, dit(x), atol=1e-5)

    tc.restore_compiled(nm)
    assert list(dit.blocks) == originals
    assert torch.equal(reference, dit(x))
    torch._dynamo.reset()


@pytest.mark.skipif(sys.platform == "win32", reason="torch.compile is unsupported on native Windows")
def test_fp8_resident_compile_matches_eager_and_restores(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    _eager_backend_compile(monkeypatch)
    torch.manual_seed(0)
    dit = _QuantDit()
    x = torch.randn(2, 5, 16)
    reference = dit(x)
    originals = list(dit.blocks)

    nm = _MockNM(dit)
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)

    assert nm._compiled is not None and nm._compiled.active
    assert all(tc.is_compiled(block) for block in dit.blocks)
    assert torch.allclose(reference, dit(x), atol=1e-4, rtol=1e-4)

    tc.restore_compiled(nm)
    assert nm._compiled is None
    assert list(dit.blocks) == originals
    assert all(not tc.is_compiled(block) for block in dit.blocks)
    assert torch.equal(reference, dit(x))
    torch._dynamo.reset()


@pytest.mark.skipif(sys.platform == "win32", reason="torch.compile is unsupported on native Windows")
def test_streamed_compile_keeps_block_hooks_eager_and_restores(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    _eager_backend_compile(monkeypatch)
    torch.manual_seed(0)
    dit = _StreamedDit(n=2)
    calls = {"pre": 0, "post": 0}
    for block in dit.blocks:
        block.register_forward_pre_hook(lambda m, a: calls.__setitem__("pre", calls["pre"] + 1))
        block.register_forward_hook(lambda m, a, o: calls.__setitem__("post", calls["post"] + 1))
    x = torch.randn(2, 5, 16)
    reference = dit(x)
    originals = list(dit.blocks)
    calls.update(pre=0, post=0)

    nm = _streaming_nm(dit)
    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)

    assert nm._compiled is not None and nm._compiled.active
    assert list(dit.blocks) == originals
    assert all("forward" in vars(block) for block in dit.blocks)
    assert all(not tc.is_compiled(block) for block in dit.blocks)
    for _ in range(3):
        out = dit(x)
    assert torch.allclose(reference, out, atol=1e-5)
    assert calls == {"pre": 6, "post": 6}

    entries = len(nm._compiled.forwards)
    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)
    assert len(nm._compiled.forwards) == entries

    tc.restore_compiled(nm)
    assert nm._compiled is None
    assert list(dit.blocks) == originals
    assert all("forward" not in vars(block) for block in dit.blocks)
    calls.update(pre=0, post=0)
    assert torch.equal(reference, dit(x))
    assert calls == {"pre": 2, "post": 2}
    torch._dynamo.reset()


def test_streamed_compile_wraps_forward_not_the_module(monkeypatch):
    _enable(monkeypatch)
    seen = []

    def fake_compile(target, **kwargs):
        seen.append((target, kwargs))
        return target

    monkeypatch.setattr(torch, "compile", fake_compile)
    dit = _StreamedDit(n=3)
    nm = _streaming_nm(dit)
    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)
    assert [kw for _t, kw in seen] == [{"mode": "default", "dynamic": True}] * 3
    assert all(getattr(t, "__self__", None) is block for (t, _kw), block in zip(seen, dit.blocks))
    assert len(nm._compiled.forwards) == 3 and nm._compiled.entries == []


# --- Codex E9-E11: compile robustness ---------------------------------------


class _FakeCompiled(nn.Module):
    """Marker stand-in for a torch.compile OptimizedModule (has ``_orig_mod``)."""

    def __init__(self, mod: nn.Module) -> None:
        super().__init__()
        self._orig_mod = mod

    def forward(self, *a, **k):
        return self._orig_mod(*a, **k)


@pytest.mark.parametrize("streaming", [False, True], ids=["resident", "streamed"])
def test_compile_blocks_skips_blocks_already_compiled(monkeypatch, streaming):
    wrapped = []

    def record(target, **kw):
        wrapped.append(target)
        return target if streaming else _FakeCompiled(target)

    monkeypatch.setattr(tc.torch, "compile", record)
    dit = _StreamedDit(n=3)
    first = tc.compile_blocks(dit, streaming=streaming)
    second = tc.compile_blocks(dit, streaming=streaming)
    assert first.active and not second.active
    assert len(wrapped) == 3


@pytest.mark.parametrize("streaming", [False, True], ids=["resident", "streamed"])
def test_compile_blocks_failure_leaves_nothing_wrapped(monkeypatch, streaming):
    dit = _StreamedDit(n=3)
    originals = list(dit.blocks)
    calls = {"n": 0}

    def flaky(target, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("inductor blew up")
        return target if streaming else _FakeCompiled(target)

    monkeypatch.setattr(tc.torch, "compile", flaky)
    with pytest.raises(RuntimeError):
        tc.compile_blocks(dit, streaming=streaming)
    assert calls["n"] == 2
    assert list(dit.blocks) == originals
    assert all(not tc.is_compiled(b) and "forward" not in vars(b) for b in dit.blocks)


def test_maybe_compile_dit_clears_handle_on_failure(monkeypatch):
    _enable(monkeypatch)
    m = _Blocks(n=3)
    nm = _MockNM(m)

    def _flaky(block, **kw):
        raise RuntimeError("compile unavailable")

    monkeypatch.setattr(tc.torch, "compile", _flaky)
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)   # must not raise
    assert nm._compiled is None
    assert all(not tc.is_compiled(b) for b in m.blocks)


def test_maybe_compile_dit_enables_dynamo_suppress_errors(monkeypatch):
    # E10: lazy first-forward compile errors must fall back to eager, not abort a
    # generation — maybe_compile_dit turns on dynamo's suppress_errors.
    import torch._dynamo

    _enable(monkeypatch)
    monkeypatch.setattr(torch._dynamo.config, "suppress_errors", False)
    monkeypatch.setattr(tc.torch, "compile", lambda block, **kw: _FakeCompiled(block))
    tc.maybe_compile_dit(_MockNM(_Blocks(n=2)), resident=True, is_cuda=True)
    assert torch._dynamo.config.suppress_errors is True


@pytest.mark.parametrize("streaming", [False, True], ids=["resident", "streamed"])
def test_stream_to_uncompiles_blocks_before_restreaming(monkeypatch, streaming):
    from src.platform.runtime.native.engine import NativeModel

    wrap = (lambda target, **kw: target) if streaming else (lambda target, **kw: _FakeCompiled(target))
    monkeypatch.setattr(tc.torch, "compile", wrap)
    dit = _StreamedDit(n=2)
    originals = list(dit.blocks)
    handle = tc.compile_blocks(dit, streaming=streaming)
    assert handle.active

    nm = NativeModel("diffusion_model", dit)
    nm._compiled = handle
    nm.stream_to("cpu", 0.0)

    assert nm._compiled is None
    assert list(dit.blocks) == originals
    assert all(not tc.is_compiled(b) and "forward" not in vars(b) for b in dit.blocks)


# --- MiniMax-Music3 AR core: whole-module reduce-overhead compile ------------


def _tiny_music3_cfg() -> MiniMaxMusic3TextEncoderConfig:
    return MiniMaxMusic3TextEncoderConfig(
        hidden_size=8, intermediate_size=12, num_layers=1, head_dim=4,
        num_attention_heads=2, num_key_value_heads=1, rope_theta=10000.0,
        rms_norm_eps=1e-6, max_position_embeddings=32,
        decoder_intermediate_size=10, decoder_num_layers=2, decoder_num_heads=2, decoder_head_dim=4,
        audio_vocab_size=6, num_codebooks=8,
        merged_qkv=False, merged_mlp=False, decoder_merged_qkv=False, decoder_merged_mlp=False,
        pruned_embeddings=False, pruned_lm_head=False,
    )


def _tiny_depth_decoder() -> DepthDecoderModule:
    decoder = DepthDecoderModule(_tiny_music3_cfg(), disable_weight_init)
    for p in decoder.parameters():
        nn.init.normal_(p)
    decoder.eval()
    return decoder


class _MockNativeLM:
    """Stand-in for the AR core's ``NativeModel`` wrapper (``lm_model`` in
    ``generator/audio_minimax_music3/main.py``): only the fields
    ``music3_ar_compile_gate``/``maybe_compile_music3_ar`` read.
    ``.module`` mimics ``MiniMaxMusic3AudioLM``'s own shape (``.model.
    audio_decoder``) without building a real global LM."""

    def __init__(self, decoder, quant_format=None) -> None:
        self.module = types.SimpleNamespace(model=types.SimpleNamespace(audio_decoder=decoder))
        self.quant_format = quant_format
        self._compiled = None


# --- gate logic (mock, fast) --------------------------------------------------


def test_music3_gate_disabled_by_default(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", False)
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(_tiny_depth_decoder()), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "disabled")


def test_music3_gate_rejects_cpu(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(_tiny_depth_decoder()), resident=True, is_cuda=False)
    assert (ok, reason) == (False, "cpu")


def test_music3_gate_rejects_streaming(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(_tiny_depth_decoder()), resident=False, is_cuda=True)
    assert (ok, reason) == (False, "streaming")


@pytest.mark.parametrize("quant_format", ["fp8_scaled", "int8_convrot", "nvfp4"])
def test_music3_gate_allows_a_quantized_lm_when_the_decoder_is_not_quantized(monkeypatch, quant_format):
    _enable(monkeypatch)
    lm = _MockNativeLM(_tiny_depth_decoder(), quant_format=quant_format)
    ok, reason = tc.music3_ar_compile_gate(lm, resident=True, is_cuda=True)
    assert (ok, reason) == (True, "ok")


def test_music3_gate_still_rejects_a_quantized_lm_with_a_quantized_decoder(monkeypatch):
    _enable(monkeypatch)
    decoder = _tiny_depth_decoder()
    decoder.projection = _fp8_linear()
    lm = _MockNativeLM(decoder, quant_format="fp8_scaled")
    ok, reason = tc.music3_ar_compile_gate(lm, resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized-linear")


def test_music3_gate_still_rejects_a_quantized_lm_when_streaming(monkeypatch):
    _enable(monkeypatch)
    lm = _MockNativeLM(_tiny_depth_decoder(), quant_format="int8_convrot")
    ok, reason = tc.music3_ar_compile_gate(lm, resident=False, is_cuda=True)
    assert (ok, reason) == (False, "streaming")


def test_music3_lm_graph_gate_reasons(monkeypatch):
    decoder = _tiny_depth_decoder()
    lm = types.SimpleNamespace(module=decoder, quant_format="int8_convrot")
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", False)
    assert tc.music3_lm_graph_gate(lm, resident=True, is_cuda=True) == (False, "disabled")
    _enable(monkeypatch)
    assert tc.music3_lm_graph_gate(lm, resident=True, is_cuda=False) == (False, "cpu")
    assert tc.music3_lm_graph_gate(lm, resident=False, is_cuda=True) == (False, "streaming")
    assert tc.music3_lm_graph_gate(lm, resident=True, is_cuda=True) == (True, "ok")
    decoder.layers[0].self_attn.o_proj.lora_deltas = [object()]
    assert tc.music3_lm_graph_gate(lm, resident=True, is_cuda=True) == (False, "runtime-lora")


def test_music3_gate_rejects_no_audio_decoder(monkeypatch):
    _enable(monkeypatch)
    lm = types.SimpleNamespace(module=types.SimpleNamespace(model=types.SimpleNamespace()), quant_format=None)
    ok, reason = tc.music3_ar_compile_gate(lm, resident=True, is_cuda=True)
    assert (ok, reason) == (False, "no-audio-decoder")


@pytest.mark.parametrize(
    "make_linear",
    [_fp8_linear, _int8_linear, _int8_convrot_linear, _nvfp4_linear],
    ids=["fp8", "int8", "int8-convrot", "nvfp4"],
)
def test_music3_gate_rejects_quantized_linear(monkeypatch, make_linear):
    _enable(monkeypatch)
    decoder = _tiny_depth_decoder()
    decoder.projection = make_linear()
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(decoder), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized-linear")


def _raw_linear(dtype: torch.dtype) -> Fp8ScaledLinear:
    layer = Fp8ScaledLinear(8, 8)
    layer.weight = nn.Parameter(torch.zeros(8, 8).to(dtype), requires_grad=False)
    return layer


@pytest.mark.parametrize(
    "dtype",
    [torch.float32, torch.bfloat16, torch.float8_e4m3fn, torch.float8_e5m2, torch.int8],
    ids=["fp32", "bf16", "fp8-e4m3", "fp8-e5m2", "int8"],
)
def test_music3_gate_rejects_a_scale_less_fp8_scaled_linear_whatever_its_weight_dtype(monkeypatch, dtype):
    _enable(monkeypatch)
    decoder = _tiny_depth_decoder()
    decoder.projection = _raw_linear(dtype)
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(decoder), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized-linear")


def test_music3_gate_rejects_runtime_lora(monkeypatch):
    _enable(monkeypatch)
    decoder = _tiny_depth_decoder()
    decoder.layers[0].self_attn.o_proj.lora_deltas = [object()]
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(decoder), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "runtime-lora")


def test_music3_gate_passes(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.music3_ar_compile_gate(_MockNativeLM(_tiny_depth_decoder()), resident=True, is_cuda=True)
    assert (ok, reason) == (True, "ok")


def test_maybe_compile_music3_ar_noop_when_gate_fails(monkeypatch):
    _enable(monkeypatch)
    lm = _MockNativeLM(_tiny_depth_decoder())
    tc.maybe_compile_music3_ar(lm, resident=False, is_cuda=True)   # streaming -> skip
    assert lm._compiled is None
    assert not tc.is_compiled(lm.module.model.audio_decoder)


def test_music3_depth_decoder_wrap_uses_reduce_overhead_static_shapes_and_restores(monkeypatch):
    _enable(monkeypatch)
    calls = _record_compile_calls(monkeypatch)
    torch.manual_seed(0)
    decoder = _tiny_depth_decoder()

    x2 = torch.randn(2, 2, decoder.cfg.hidden_size)   # depth decoder's own seq_len=2 start
    x3 = torch.randn(2, 3, decoder.cfg.hidden_size)   # one residual step further
    with torch.no_grad():
        reference2 = decoder(x2)
        reference3 = decoder(x3)

    lm = _MockNativeLM(decoder)
    tc.maybe_compile_music3_ar(lm, resident=True, is_cuda=True)

    assert calls == [{"mode": "reduce-overhead", "dynamic": False}]
    assert lm._compiled is not None and lm._compiled.active
    compiled_decoder = lm.module.model.audio_decoder
    assert tc.is_compiled(compiled_decoder)
    # The steady-state shape range (7 lengths) must fit the recompile budget.
    assert torch._dynamo.config.cache_size_limit >= tc._MUSIC3_DEPTH_DECODER_SHAPE_COUNT

    with torch.no_grad():
        compiled_out2 = compiled_decoder(x2)
        compiled_out3 = compiled_decoder(x3)
    assert torch.allclose(reference2, compiled_out2, atol=1e-5)
    assert torch.allclose(reference3, compiled_out3, atol=1e-5)

    # Idempotent: a second call does not re-wrap.
    entries = len(lm._compiled.entries)
    tc.maybe_compile_music3_ar(lm, resident=True, is_cuda=True)
    assert len(lm._compiled.entries) == entries

    # Restore puts the exact original module back (identity check).
    tc.restore_compiled(lm)
    assert lm._compiled is None
    assert lm.module.model.audio_decoder is decoder
    assert not tc.is_compiled(lm.module.model.audio_decoder)
    with torch.no_grad():
        assert torch.allclose(reference2, decoder(x2), atol=1e-5)
