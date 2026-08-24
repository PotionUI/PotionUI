"""Tests for regional torch.compile (optimizations/compile.py).

Gate logic is mock-based and fast. Block discovery is checked against a REAL
(tiny) Flux. The compile→execute→restore path is a real CPU torch.compile smoke
(inductor works on CPU) on a controllable block module — Flux's forward input
contract is intricate, so the arch is exercised for *discovery* and a plain
homogeneous-block module for *execution parity*, which together cover both halves
without wrestling Flux's packed-latent inputs. The execute test is ~20s, so it is
marked ``slow``.
"""

from __future__ import annotations

import types

import torch
import torch.nn as nn
import pytest

from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.arch.minimax_music3.config import MiniMaxMusic3TextEncoderConfig
from src.platform.runtime.native.arch.minimax_music3.depth_decoder import DepthDecoderModule
from vendor.gpl.comfyui.ops import disable_weight_init, Fp8ScaledLinear
from src.platform.runtime.native.optimizations import compile as tc


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
    """Stand-in for a NativeModel: only the fields the gate/compile path read."""

    def __init__(self, module: nn.Module, quant_format=None) -> None:
        self.module = module
        self.quant_format = quant_format
        self._compiled = None


def _enable(monkeypatch):
    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "on")


# --- env toggle ---------------------------------------------------------------


def test_env_toggle_parsing(monkeypatch):
    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
    assert tc.torch_compile_enabled() is False
    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "on")
    assert tc.torch_compile_enabled() is True
    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "auto")
    assert tc.torch_compile_enabled() is True
    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "nonsense")
    assert tc.torch_compile_enabled() is False


def test_admin_override_beats_env(monkeypatch):
    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
    monkeypatch.setattr(tc, "_compile_override", None)

    tc.set_torch_compile_override("on")
    assert tc.torch_compile_enabled() is True      # no env needed

    tc.set_torch_compile_override("off")
    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "on")
    assert tc.torch_compile_enabled() is False     # explicit off beats env

    tc.set_torch_compile_override(None)
    assert tc.torch_compile_enabled() is True      # cleared -> env fallback
    tc.set_torch_compile_override("")
    assert tc.torch_compile_enabled() is True      # empty setting -> env fallback
    assert tc.get_torch_compile_override() is None


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


# --- gate logic (mock, fast) --------------------------------------------------


def test_gate_disabled_by_default(monkeypatch):
    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
    ok, reason = tc.compile_gate(_MockNM(_Blocks()), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "disabled")


def test_gate_rejects_cpu(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.compile_gate(_MockNM(_Blocks()), resident=True, is_cuda=False)
    assert (ok, reason) == (False, "cpu")


def test_gate_rejects_streaming(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.compile_gate(_MockNM(_Blocks()), resident=False, is_cuda=True)
    assert (ok, reason) == (False, "streaming")


def test_gate_rejects_quantized_by_format(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.compile_gate(_MockNM(_Blocks(), quant_format="fp8_scaled"), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized")


def test_gate_rejects_quantized_linear(monkeypatch):
    _enable(monkeypatch)

    class _WithFp8(nn.Module):
        def __init__(self):
            super().__init__()
            self.blocks = nn.ModuleList(disable_weight_init.Linear(4, 4) for _ in range(2))
            self.q = Fp8ScaledLinear(4, 4)

    ok, reason = tc.compile_gate(_MockNM(_WithFp8()), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized-linear")


def test_gate_rejects_runtime_lora(monkeypatch):
    _enable(monkeypatch)
    m = _Blocks(n=2)
    m.blocks[0].lora_deltas = [object()]   # non-empty runtime delta -> graph-break risk
    ok, reason = tc.compile_gate(_MockNM(m), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "runtime-lora")


def test_gate_rejects_no_block_lists(monkeypatch):
    _enable(monkeypatch)

    class _Flat(nn.Module):
        def __init__(self):
            super().__init__()
            self.lin = disable_weight_init.Linear(4, 4)

    ok, reason = tc.compile_gate(_MockNM(_Flat()), resident=True, is_cuda=True)
    assert (ok, reason) == (False, "no-block-lists")


def test_gate_passes_for_float_resident_dit(monkeypatch):
    _enable(monkeypatch)
    ok, reason = tc.compile_gate(_MockNM(_Blocks(n=3)), resident=True, is_cuda=True)
    assert (ok, reason) == (True, "ok")


# --- maybe_compile_dit gating (no real compile) -------------------------------


def test_maybe_compile_noop_when_gate_fails(monkeypatch):
    _enable(monkeypatch)
    nm = _MockNM(_Blocks())
    tc.maybe_compile_dit(nm, resident=False, is_cuda=True)   # streaming -> skip
    assert nm._compiled is None
    for blk in nm.module.blocks:
        assert not tc.is_compiled(blk)


# --- compile → execute → restore (real CPU compile) ---------------------------


@pytest.mark.slow
def test_compile_executes_matches_and_restores(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    torch.manual_seed(0)
    m = _Blocks(n=3, dim=8)
    x = torch.randn(2, 5, 8)
    reference = m(x)
    originals = list(m.blocks)

    nm = _MockNM(m)
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)

    # Blocks are now compiled wrappers, and a real forward matches eager numerics.
    assert nm._compiled is not None and nm._compiled.active
    assert all(tc.is_compiled(blk) for blk in m.blocks)
    compiled_out = m(x)
    assert torch.allclose(reference, compiled_out, atol=1e-5)

    # Idempotent: a second call does not re-wrap (no double compilation).
    handle_entries = len(nm._compiled.entries)
    tc.maybe_compile_dit(nm, resident=True, is_cuda=True)
    assert len(nm._compiled.entries) == handle_entries
    assert all(not tc.is_compiled(blk._orig_mod) for blk in m.blocks)

    # Restore puts the exact original block objects back (identity check) and
    # numerics still match.
    tc.restore_compiled(nm)
    assert nm._compiled is None
    assert list(m.blocks) == originals
    assert all(not tc.is_compiled(blk) for blk in m.blocks)
    assert torch.allclose(reference, m(x), atol=1e-5)
    torch._dynamo.reset()


# --- Codex E9-E11: compile robustness ---------------------------------------


class _FakeCompiled(nn.Module):
    """Marker stand-in for a torch.compile OptimizedModule (has ``_orig_mod``)."""

    def __init__(self, mod: nn.Module) -> None:
        super().__init__()
        self._orig_mod = mod

    def forward(self, *a, **k):
        return self._orig_mod(*a, **k)


def test_compile_blocks_restores_partial_wraps_on_failure(monkeypatch):
    # E11: a failure partway through wrapping must leave NO untracked compiled
    # wrappers installed (else offload/unload could never restore them).
    _enable(monkeypatch)
    m = _Blocks(n=3)
    originals = list(m.blocks)
    calls = {"n": 0}

    def _flaky(block, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("inductor blew up")
        return _FakeCompiled(block)

    monkeypatch.setattr(tc.torch, "compile", _flaky)
    with pytest.raises(RuntimeError):
        tc.compile_blocks(m)
    # every block back to its original object — nothing left wrapped.
    assert list(m.blocks) == originals
    assert all(not tc.is_compiled(b) for b in m.blocks)


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


def test_stream_to_restores_compiled_blocks(monkeypatch):
    # E9: switching a compiled resident DiT to partial-residency streaming must
    # un-compile first (streaming swaps leaf weights per forward — incompatible
    # with a compiled graph). NativeModel.stream_to restores before applying.
    from src.platform.runtime.native.engine import NativeModel

    m = _Blocks(n=3)
    monkeypatch.setattr(tc.torch, "compile", lambda block, **kw: _FakeCompiled(block))
    handle = tc.compile_blocks(m)
    assert handle.active and all(tc.is_compiled(b) for b in m.blocks)

    nm = NativeModel("diffusion_model", m)
    nm._compiled = handle
    nm.stream_to("cpu", 0.0)   # non-cuda -> falls to move_to, but must restore first

    assert nm._compiled is None
    assert all(not tc.is_compiled(b) for b in m.blocks)


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
    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
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


def test_music3_gate_rejects_quantized_by_format(monkeypatch):
    _enable(monkeypatch)
    lm = _MockNativeLM(_tiny_depth_decoder(), quant_format="fp8_scaled")
    ok, reason = tc.music3_ar_compile_gate(lm, resident=True, is_cuda=True)
    assert (ok, reason) == (False, "quantized")


def test_music3_gate_rejects_no_audio_decoder(monkeypatch):
    _enable(monkeypatch)
    lm = types.SimpleNamespace(module=types.SimpleNamespace(model=types.SimpleNamespace()), quant_format=None)
    ok, reason = tc.music3_ar_compile_gate(lm, resident=True, is_cuda=True)
    assert (ok, reason) == (False, "no-audio-decoder")


def test_music3_gate_rejects_quantized_linear(monkeypatch):
    _enable(monkeypatch)
    decoder = _tiny_depth_decoder()
    decoder.projection = Fp8ScaledLinear(8, 8)
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


# --- compile -> execute (multiple shapes) -> restore (real CPU compile) ------


@pytest.mark.slow
def test_music3_depth_decoder_compile_executes_at_two_shapes_and_restores(monkeypatch):
    torch._dynamo.reset()
    _enable(monkeypatch)
    torch.manual_seed(0)
    decoder = _tiny_depth_decoder()

    x2 = torch.randn(2, 2, decoder.cfg.hidden_size)   # depth decoder's own seq_len=2 start
    x3 = torch.randn(2, 3, decoder.cfg.hidden_size)   # one residual step further
    with torch.no_grad():
        reference2 = decoder(x2)
        reference3 = decoder(x3)

    lm = _MockNativeLM(decoder)
    tc.maybe_compile_music3_ar(lm, resident=True, is_cuda=True)

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
    torch._dynamo.reset()
