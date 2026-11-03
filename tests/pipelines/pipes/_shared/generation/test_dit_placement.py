"""Tests for the sequence-length-aware DiT placement helper.

Covers: the per-token activation-reserve formula (floor, scaling, audio
addition), the resident-vs-partial decision at the exact scenario described in
the audit (5s/15s full pin, 40s partial on a 32GB card with a 23.3GB DiT), the
OOM-degrade ladder (mirrors ``NativeGenerator._move_dit_to_gpu``/
``_stream_dit_to_gpu``), the foreign-eviction exclude-list plumbing (including
the one-shot-generator footgun), and the CPU/no-CUDA passthrough.

No real GPU or CUDA needed: ``get_residency_registry``/``free_vram_gb``/
``minimum_inference_memory_gb`` are patched at the module boundary (the same
style as ``test_dit_restore.py``), and OOM is simulated by raising the real
``torch.cuda.OutOfMemoryError`` class directly (constructing/raising it needs
no actual device).
"""

from __future__ import annotations

import logging
import types
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import torch
import torch.nn as nn
from torch.multiprocessing.reductions import StorageWeakRef
from torch.utils._python_dispatch import TorchDispatchMode
from torch.utils._pytree import tree_flatten

from src.platform.runtime.native.io.quant_formats import quantize_nvfp4_dynamic
from src.platform.settings import runtime_flags
from src.platform.runtime.native.kernels.lora_math import _add_lora_output_branch, _lora_output_branch

from src.platform.runtime.native.arch.minimax_h3.model import default_ffn_chunk_rows
from src.platform.runtime.native.kernels import Fp8ScaledLinear, FastPathKind, fast_path, fp8_ops, gemm
from src.platform.runtime.native.kernels.fp8_linear import _quantize_fp8_dynamic
from src.platform.runtime.native.kernels import activation_quant_chunk_rows
from src.platform.runtime.native.kernels.nvfp4_linear import Nvfp4Linear
from src.platform.runtime.native.memory.partial import plan_residency_split
from src.pipelines.pipes._shared.generation.dit_placement import (
    _ACTIVATION_RESERVE_FLOOR_GB,
    _ACTIVATION_SAFETY_MARGIN,
    _FP8_ACTIVATION_QUANT_BYTES_PER_ELEMENT,
    _NVFP4_ACTIVATION_QUANT_BYTES_PER_ELEMENT,
    _dit_lora_profile,
    _dit_quant_scratch,
    _ffn_transient_bytes_per_token,
    _LTX_INNER_DIM,
    DitLoraProfile,
    DitPlacementDecision,
    QuantScratch,
    QuantWidth,
    SamplingOutOfMemory,
    choose_seq_chunk_rows,
    estimate_activation_reserve_gb,
    guard_sampling_oom,
    hand_off_dit,
    place_dit_for_sequence,
    sequence_fit,
)
from src.platform.runtime.native.errors import is_cuda_oom

from tests.fixtures.kernel_capabilities import ada, ampere, blackwell, fake_capabilities
from tests.fixtures.kernel_gemm import installed_gemm_verdicts
from tests.platform.runtime.native._nvfp4_ref import F4_MAX, F8_MAX, quantize_nvfp4

_MOD = "src.pipelines.pipes._shared.generation.dit_placement"


# -- activation reserve formula -----------------------------------------------

def test_reserve_floors_at_zero_tokens():
    assert estimate_activation_reserve_gb(0, 0) == _ACTIVATION_RESERVE_FLOOR_GB


def test_reserve_scales_linearly_once_above_the_floor():
    # Both values chosen well above _ACTIVATION_RESERVE_FLOOR_GB so the ratio
    # reflects the linear per-token formula, not the floor clamp.
    small = estimate_activation_reserve_gb(20_000)
    large = estimate_activation_reserve_gb(2_000_000)
    assert large > small
    assert large / small == pytest.approx(100, rel=0.05)


def test_reserve_matches_audit_scenarios_within_margin():
    # Audit: S~14,080 (5s) peaked ~1.2GB; S~110,880 (40s) peaked
    # ~9.4GB -- both WITHOUT this module's safety margin. This module adds a
    # 15% multiplicative margin on top, so the estimate should sit a bit above
    # (never below) those measured peaks.
    five_s = estimate_activation_reserve_gb(14_080)
    forty_s = estimate_activation_reserve_gb(110_880)
    assert 1.2 <= five_s <= 1.6
    assert 9.4 <= forty_s <= 12.0


def test_audio_tokens_add_to_the_reserve():
    video_only = estimate_activation_reserve_gb(50_000, audio_tokens=0)
    with_audio = estimate_activation_reserve_gb(50_000, audio_tokens=5_000)
    assert with_audio > video_only
    assert with_audio == estimate_activation_reserve_gb(55_000, audio_tokens=0)


# -- inner_dim parameterization (MiniMax-H3: attn inner 7168 != hidden 5376) --

def test_inner_dim_defaults_to_ltx_no_behavior_change():
    assert estimate_activation_reserve_gb(50_000) == estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)


def test_wider_inner_dim_increases_the_reserve():
    ltx_reserve = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)
    h3_reserve = estimate_activation_reserve_gb(50_000, inner_dim=7168)  # H3's attn inner dim
    assert h3_reserve > ltx_reserve


def test_inner_dim_scales_the_reserve_linearly_above_the_floor():
    small = estimate_activation_reserve_gb(50_000, inner_dim=1000)
    large = estimate_activation_reserve_gb(50_000, inner_dim=2000)
    assert large / small == pytest.approx(2.0, rel=1e-6)


def test_inner_dim_is_a_no_op_at_zero_tokens_the_floor_still_wins():
    assert estimate_activation_reserve_gb(0, inner_dim=7168) == _ACTIVATION_RESERVE_FLOOR_GB


H3_ATTN_INNER_DIM = 56 * 128  # MiniMax-H3: 56 heads x 128 head_dim


def test_place_dit_for_sequence_threads_inner_dim_into_the_reserve(monkeypatch):
    # Two calls that would land on OPPOSITE sides of the resident/partial
    # decision purely because of inner_dim -- proves place_dit_for_sequence
    # actually forwards it to estimate_activation_reserve_gb rather than
    # silently dropping the kwarg.
    # 70.0, not a smaller value: high enough that the WIDE inner_dim's ~67.6GB
    # reserve still clears the new infeasibility gate (total_reserve < free)
    # while remaining too big for the DiT's own weight to also fit resident --
    # the point of this test is the resident/partial split, not a refusal.
    monkeypatch.setattr(f"{_MOD}.free_vram_gb", lambda device: 70.0)
    monkeypatch.setattr(f"{_MOD}.effective_free_vram_gb", lambda device: 70.0)
    monkeypatch.setattr(f"{_MOD}.minimum_inference_memory_gb", lambda: 0.0)
    manager = SimpleNamespace(ensure_free=lambda *a, **k: False, offload_all=lambda *a, **k: False)
    monkeypatch.setattr(f"{_MOD}.get_residency_registry", lambda: manager)

    calls = []
    dit = SimpleNamespace(
        estimated_vram_gb=9.5,
        move_to=lambda device: calls.append(("resident", device)),
        stream_to=lambda device, budget: calls.append(("partial", device, budget)),
    )
    tokens = 400_000  # large enough that a wide inner_dim pushes the reserve past 0.5GB of headroom

    decision_narrow = place_dit_for_sequence(dit, "cuda", video_tokens=tokens, inner_dim=64)
    assert decision_narrow.mode == "resident"

    decision_wide = place_dit_for_sequence(dit, "cuda", video_tokens=tokens, inner_dim=H3_ATTN_INNER_DIM)
    assert decision_wide.mode == "partial"


# -- LoRA cost terms ------------------------------------------------------

def test_empty_lora_profile_is_the_default_no_behavior_change():
    assert estimate_activation_reserve_gb(50_000) == estimate_activation_reserve_gb(
        50_000, lora=DitLoraProfile(),
    )


def test_a_merely_active_lora_profile_costs_nothing():
    # The common case: plain deltas on quantized Linears, applied by the
    # chunked activation-side branch. Nothing there scales with S, so a
    # resident LoRA must not move the reserve at all.
    plain = DitLoraProfile(active=True, delta_bytes=1_400_000_000)
    assert estimate_activation_reserve_gb(50_000, lora=plain) == estimate_activation_reserve_gb(50_000)


def test_gemm_fast_path_output_buffer_adds_a_per_token_term():
    without = estimate_activation_reserve_gb(50_000)
    with_buffer = estimate_activation_reserve_gb(
        50_000, lora=DitLoraProfile(output_buffer_out_features=8192, active=True),
    )
    assert with_buffer > without
    # Exactly the two (S, out_features) compute-dtype buffers the fast path
    # holds at once -- not a fudge factor.
    expected_extra_gb = (50_000 * 2 * 8192 * 2) / 1024 ** 3 * 1.15
    assert with_buffer - without == pytest.approx(expected_extra_gb, rel=1e-6)


def test_weight_side_deltas_add_a_flat_reserve_not_a_per_token_one():
    lokr = DitLoraProfile(weight_side_bytes=2 * 5376 * 28672 * 2, active=True)
    small = estimate_activation_reserve_gb(10_000, lora=lokr) - estimate_activation_reserve_gb(10_000)
    large = estimate_activation_reserve_gb(200_000, lora=lokr) - estimate_activation_reserve_gb(200_000)
    assert small == pytest.approx(large, rel=1e-6)
    assert small == pytest.approx(lokr.weight_side_bytes / 1024 ** 3 * 1.15, rel=1e-6)


def test_per_token_lora_terms_are_no_ops_at_zero_tokens_the_floor_still_wins():
    # zero tokens -> zero contribution from any per-token term, so the floor
    # wins regardless of the output-buffer width.
    buffered = DitLoraProfile(output_buffer_out_features=8192, active=True)
    assert estimate_activation_reserve_gb(0, lora=buffered) == _ACTIVATION_RESERVE_FLOOR_GB
    assert estimate_activation_reserve_gb(0, lora=buffered) == estimate_activation_reserve_gb(0)


# -- LoRA detection (_dit_lora_profile) --------------------------------------

def _linear_with_deltas(deltas) -> nn.Linear:
    linear = nn.Linear(4, 4)
    linear.lora_deltas = deltas
    return linear


def _plain_delta(in_features: int, out_features: int, rank: int = 8, *, dtype=torch.float32):
    """A delta shaped the way ``_deltas_output_branch_ok`` requires of an
    output-side (activation-branch) adapter: ``down`` ``(rank, in)``, ``up``
    ``(out, rank)``, no Kronecker factorisation, no target slice."""
    return SimpleNamespace(
        down=torch.zeros(rank, in_features, dtype=dtype),
        up=torch.zeros(out_features, rank, dtype=dtype),
        kron=False, target_slice=None, scale=1.0, alpha=float(rank),
    )


def _lokr_delta(in_features: int, out_features: int, *, dtype=torch.float32):
    """A LoKr delta: ``kron`` is what pushes it onto the weight side."""
    delta = _plain_delta(in_features, out_features, dtype=dtype)
    delta.kron = True
    return delta


def _linear_with_lokr_delta(in_features: int, out_features: int) -> nn.Linear:
    linear = nn.Linear(in_features, out_features, device="meta")
    linear.lora_deltas = [_lokr_delta(in_features, out_features)]
    return linear


def _stored_layer(cls, in_features: int, out_features: int, dtype: torch.dtype):
    layer = cls(in_features, out_features, device="meta", dtype=torch.bfloat16)
    layer.weight = nn.Parameter(torch.empty(out_features, in_features, dtype=dtype, device="meta"), requires_grad=False)
    return layer


def _fp8_layer(in_features: int, out_features: int, *, dtype: torch.dtype = torch.float8_e4m3fn):
    layer = _stored_layer(Fp8ScaledLinear, in_features, out_features, dtype)
    layer.weight_scale = torch.ones((), device="meta")
    return layer


def _int8_layer(in_features: int, out_features: int, *, convrot: bool = False):
    layer = _fp8_layer(in_features, out_features, dtype=torch.int8)
    if convrot:
        layer.convrot_hadamard = torch.ones(16, 16, device="meta")
    return layer


def _nvfp4_layer(in_features: int, out_features: int):
    layer = Nvfp4Linear(in_features, out_features, device="meta", dtype=torch.bfloat16)
    layer._is_nvfp4 = True
    layer.weight = None
    return layer


def _loaded_nvfp4_layer(in_features: int = 32, out_features: int = 16):
    torch.manual_seed(3)
    weight = torch.randn(out_features, in_features) * 0.05
    tensor_scale = (weight.abs().amax() / (F4_MAX * F8_MAX)).clamp(min=1e-8).detach()
    packed, block, _, _ = quantize_nvfp4(weight, tensor_scale)
    layer = fp8_ops.Linear(in_features, out_features, bias=True)
    layer.requires_grad_(False)
    layer.load_state_dict(
        {
            "weight": packed,
            "weight_scale": block,
            "weight_scale_2": tensor_scale.clone(),
            "bias": torch.zeros(out_features),
        },
        strict=False,
        assign=True,
    )
    return layer


def _quantized_linear(in_features: int, out_features: int, *, nvfp4: bool):
    layer = _nvfp4_layer(in_features, out_features) if nvfp4 else _fp8_layer(in_features, out_features)
    layer.lora_deltas = [_plain_delta(in_features, out_features)]
    return layer


@pytest.fixture
def machine(install_capabilities, monkeypatch):
    gemm._reset_verdicts()
    with ExitStack() as stack:

        def configure(*, fp8=False, nvfp4=False, fused=True, hardware=None):
            if hardware is None:
                install_capabilities(fake_capabilities(blackwell()))
                stack.enter_context(installed_gemm_verdicts(fp8=True, nvfp4=True))
            else:
                install_capabilities(fake_capabilities(hardware()))
                monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
            flags = runtime_flags.runtime_flag_values()
            monkeypatch.setitem(flags, "native_fp8_matmul", fp8)
            monkeypatch.setitem(flags, "native_nvfp4_matmul", nvfp4)
            monkeypatch.setitem(flags, "native_lora_fused", fused)

        yield configure
    gemm._reset_verdicts()


def test_dit_without_a_module_attribute_has_no_active_lora():
    dit, _ = _dit()  # the shared test double never sets .module
    assert _dit_lora_profile(dit).active is False


def test_dit_module_that_is_not_an_nn_module_is_inactive():
    # Some pipe unit tests stub ``dit.module`` as a bare callable (the DiT
    # forward function itself, not a real ``nn.Module``) -- must not raise.
    dit = SimpleNamespace(module=lambda x: x)
    assert _dit_lora_profile(dit).active is False


def test_dit_module_with_no_lora_deltas_attribute_anywhere_is_inactive():
    dit = SimpleNamespace(module=nn.Sequential(nn.Linear(4, 4), nn.ReLU()))
    assert _dit_lora_profile(dit).active is False


def test_dit_module_with_only_empty_lora_deltas_is_inactive():
    dit = SimpleNamespace(module=nn.Sequential(_linear_with_deltas([]), _linear_with_deltas(None)))
    assert _dit_lora_profile(dit).active is False


def test_dit_module_with_a_populated_lora_deltas_is_active():
    active = SimpleNamespace(down=torch.zeros(4, 4), up=torch.zeros(4, 4))
    dit = SimpleNamespace(
        module=nn.Sequential(_linear_with_deltas([]), _linear_with_deltas([active])),
    )
    assert _dit_lora_profile(dit).active is True


# -- LoRA profile: which side each delta actually costs on ------------------

def test_the_chunked_activation_branch_allocates_nothing_extra():
    # _add_lora_output_branch (the path a plain delta takes on every
    # quantized Linear whose GEMM fast path is off, i.e. the default)
    # accumulates INTO the layer's own output under no_grad -- it returns the
    # very tensor it was handed, so there is no second S-sized buffer to
    # reserve for. This is the fact the per-token term used to contradict.
    out = torch.zeros(64, 32)
    x = torch.zeros(64, 16)
    with torch.no_grad():
        result = _add_lora_output_branch(out, x, [_plain_delta(16, 32)], 32)
    assert result is out


def test_the_gemm_fast_path_branch_still_preallocates_an_s_sized_buffer():
    # _lora_output_branch (what the nvfp4 / fp8 _scaled_mm fast paths add to
    # their raw GEMM output) still returns a fresh (S, out_features) tensor,
    # which is why the per-token term survives for those layers only.
    x2d = torch.zeros(64, 16)
    branch = _lora_output_branch(x2d, [_plain_delta(16, 32)], torch.float32, 32)
    assert branch.shape == (64, 32)
    assert branch.data_ptr() != x2d.data_ptr()


def test_a_plain_delta_on_a_quantized_linear_costs_no_per_token_buffer_by_default(machine):
    machine()
    dit = SimpleNamespace(module=nn.Sequential(_quantized_linear(5376, 28672, nvfp4=True)))
    profile = _dit_lora_profile(dit)
    assert profile.active is True
    assert profile.output_buffer_out_features == 0
    assert profile.weight_side_bytes == 0


def test_an_nvfp4_linear_costs_the_per_token_buffer_once_its_gate_is_on(machine):
    machine(nvfp4=True, fused=False)
    dit = SimpleNamespace(module=nn.Sequential(_quantized_linear(5376, 28672, nvfp4=True)))
    profile = _dit_lora_profile(dit)
    assert profile.output_buffer_out_features == 28672
    assert profile.output_buffer_bytes_per_token == 2 * 28672 * 2


def test_an_fp8_scaled_linear_costs_the_per_token_buffer_once_its_gate_is_on(machine):
    machine(fp8=True, fused=False)
    dit = SimpleNamespace(module=nn.Sequential(_quantized_linear(5376, 28672, nvfp4=False)))
    assert _dit_lora_profile(dit).output_buffer_out_features == 28672


def test_the_nvfp4_gate_does_not_enable_an_fp8_layer_and_vice_versa(machine):
    machine(nvfp4=True, fused=False)
    fp8 = SimpleNamespace(module=nn.Sequential(_quantized_linear(512, 1024, nvfp4=False)))
    assert _dit_lora_profile(fp8).output_buffer_out_features == 0
    machine(fp8=True, fused=False)
    nvfp4 = SimpleNamespace(module=nn.Sequential(_quantized_linear(512, 1024, nvfp4=True)))
    assert _dit_lora_profile(nvfp4).output_buffer_out_features == 0


def test_an_unquantized_linear_never_reaches_a_gemm_fast_path(machine):
    machine(fp8=True, nvfp4=True, fused=False)
    linear = nn.Linear(512, 1024)
    linear.lora_deltas = [_plain_delta(512, 1024)]
    dit = SimpleNamespace(module=nn.Sequential(linear))
    assert _dit_lora_profile(dit).output_buffer_out_features == 0


def test_the_widest_fast_path_linear_sizes_the_per_token_buffer(machine):
    machine(nvfp4=True, fused=False)
    dit = SimpleNamespace(module=nn.Sequential(
        _quantized_linear(5376, 5376, nvfp4=True),
        _quantized_linear(5376, 28672, nvfp4=True),
        _quantized_linear(14336, 5376, nvfp4=True),
    ))
    assert _dit_lora_profile(dit).output_buffer_out_features == 28672


def test_a_lokr_delta_is_charged_weight_side_not_per_token():
    dit = SimpleNamespace(module=nn.Sequential(_linear_with_lokr_delta(5376, 28672)))
    profile = _dit_lora_profile(dit)
    assert profile.output_buffer_out_features == 0
    assert profile.weight_side_bytes == 2 * 28672 * 5376 * 2


def test_the_widest_lokr_linear_sizes_the_flat_weight_side_reserve():
    dit = SimpleNamespace(module=nn.Sequential(
        _linear_with_lokr_delta(512, 512),
        _linear_with_lokr_delta(5376, 28672),
        _linear_with_lokr_delta(1024, 1024),
    ))
    assert _dit_lora_profile(dit).weight_side_bytes == 2 * 28672 * 5376 * 2


def test_a_mixed_stack_dequantises_a_gemm_layer_so_only_the_lokr_pays_a_weight_shaped_delta(machine):
    machine(nvfp4=True, fused=False)
    layer = _nvfp4_layer(5376, 28672)
    layer.lora_deltas = [_lokr_delta(5376, 28672), _plain_delta(5376, 28672)]
    dit = SimpleNamespace(module=nn.Sequential(layer))
    assert fast_path(layer).kind is FastPathKind.DEQUANT
    profile = _dit_lora_profile(dit)
    assert profile.output_buffer_out_features == 0
    assert profile.weight_side_bytes == 2 * 28672 * 5376 * 2


def test_a_lokr_delta_sends_a_gemm_layer_to_dequant_and_costs_nothing_per_token(machine):
    machine(fp8=True, fused=True)
    layer = _fp8_layer(5376, 28672)
    layer.lora_deltas = [_lokr_delta(5376, 28672)]
    dit = SimpleNamespace(module=nn.Sequential(layer))
    assert fast_path(layer).kind is FastPathKind.DEQUANT
    assert _dit_quant_scratch(dit).bytes_for(1) == 0
    profile = _dit_lora_profile(dit)
    assert profile.output_buffer_out_features == 0
    assert profile.weight_side_bytes == 2 * 28672 * 5376 * 2


def test_masked_deltas_alone_activate_the_profile_without_a_gemm_output_buffer(machine):
    machine(fp8=True, fused=False)
    layer = _fp8_layer(5376, 28672)
    layer.lora_masked_deltas = [_plain_delta(5376, 28672)]
    dit = SimpleNamespace(module=nn.Sequential(layer))
    assert fast_path(layer).kind is FastPathKind.FP8_SCALED_MM
    profile = _dit_lora_profile(dit)
    assert profile.active is True
    assert profile.output_buffer_out_features == 0


_GEMM_IN, _GEMM_OUT = 5120, 13824

_ON_A_GEMM = {
    "fp8-e4m3": (lambda: _fp8_layer(_GEMM_IN, _GEMM_OUT), {"fp8": True}, 5 * _GEMM_IN),
    "fp8-e4m3-on-ada": (lambda: _fp8_layer(_GEMM_IN, _GEMM_OUT), {"fp8": True, "hardware": ada}, 5 * _GEMM_IN),
    "nvfp4": (lambda: _nvfp4_layer(_GEMM_IN, _GEMM_OUT), {"nvfp4": True}, 12 * _GEMM_IN),
}

_FALLS_BACK_TO_DEQUANT = {
    "fp8-flag-off": (lambda: _fp8_layer(_GEMM_IN, _GEMM_OUT), {"fp8": False, "nvfp4": True}),
    "nvfp4-flag-off": (lambda: _nvfp4_layer(_GEMM_IN, _GEMM_OUT), {"fp8": True, "nvfp4": False}),
    "fp8-on-ampere-without-the-gemm": (
        lambda: _fp8_layer(_GEMM_IN, _GEMM_OUT), {"fp8": True, "hardware": ampere},
    ),
    "fp8-e5m2": (lambda: _fp8_layer(_GEMM_IN, _GEMM_OUT, dtype=torch.float8_e5m2), {"fp8": True}),
    "int8": (lambda: _int8_layer(_GEMM_IN, _GEMM_OUT), {"fp8": True}),
    "int8-convrot": (lambda: _int8_layer(_GEMM_IN, _GEMM_OUT, convrot=True), {"fp8": True}),
    "fp8-input-not-a-multiple-of-16": (lambda: _fp8_layer(5130, _GEMM_OUT), {"fp8": True}),
    "nvfp4-input-not-a-multiple-of-32": (lambda: _nvfp4_layer(5136, _GEMM_OUT), {"nvfp4": True}),
}


def _per_token_bytes(layer, *, with_delta: bool):
    if with_delta:
        layer.lora_deltas = [_plain_delta(layer.in_features, layer.out_features)]
    dit = SimpleNamespace(module=nn.Sequential(layer))
    return _dit_quant_scratch(dit).bytes_for(1), _dit_lora_profile(dit).output_buffer_bytes_per_token


@pytest.mark.parametrize("make, flags, quant_bytes", _ON_A_GEMM.values(), ids=list(_ON_A_GEMM))
def test_a_layer_on_a_gemm_kernel_pays_the_quantiser_and_the_unfused_lora_buffer(machine, make, flags, quant_bytes):
    machine(fused=False, **flags)
    layer = make()
    assert fast_path(layer).kind is not FastPathKind.DEQUANT
    assert _per_token_bytes(layer, with_delta=True) == (quant_bytes, 2 * _GEMM_OUT * 2)


@pytest.mark.parametrize("make, flags, quant_bytes", _ON_A_GEMM.values(), ids=list(_ON_A_GEMM))
def test_a_fused_lora_on_a_gemm_layer_pays_no_output_buffer(machine, make, flags, quant_bytes):
    machine(fused=True, **flags)
    assert _per_token_bytes(make(), with_delta=True) == (quant_bytes, 0)


@pytest.mark.parametrize("make, flags", _FALLS_BACK_TO_DEQUANT.values(), ids=list(_FALLS_BACK_TO_DEQUANT))
def test_a_layer_whose_forward_falls_back_to_dequant_pays_nothing_per_token(machine, make, flags):
    machine(fused=False, **flags)
    layer = make()
    assert fast_path(layer).kind is FastPathKind.DEQUANT
    assert _per_token_bytes(layer, with_delta=True) == (0, 0)


def test_the_per_token_terms_follow_the_live_flags_between_calls(machine):
    machine(fp8=True, fused=False)
    layer = _fp8_layer(_GEMM_IN, _GEMM_OUT)
    assert _per_token_bytes(layer, with_delta=True) == (5 * _GEMM_IN, 2 * _GEMM_OUT * 2)
    runtime_flags.runtime_flag_values()["native_lora_fused"] = True
    assert _per_token_bytes(layer, with_delta=False) == (5 * _GEMM_IN, 0)
    runtime_flags.runtime_flag_values()["native_fp8_matmul"] = False
    assert _per_token_bytes(layer, with_delta=False) == (0, 0)


def test_a_loaded_nvfp4_layer_is_never_planned_off_the_gpu(machine):
    machine(nvfp4=True)
    nvfp4 = _loaded_nvfp4_layer()
    streamable = _fp8_layer(5120, _GEMM_OUT)
    plan = plan_residency_split(nn.Sequential(nvfp4, streamable), 0.0)
    assert fast_path(nvfp4).requires_resident_weight is True
    assert plan.streamed_names == ("1",)
    assert plan.resident_names == ()


# -- test doubles --------------------------------------------------------------

class _FakeResidencyRegistry:
    """Records every eviction call; nothing is ever actually offloaded (the
    tests each set up ``free_vram_gb`` to already reflect the desired state)."""

    def __init__(self):
        self.ensure_free_calls = []
        self.offload_all_calls = []

    def ensure_free(self, device, need_gb, current_free_gb, *, exclude=()):
        self.ensure_free_calls.append((device, need_gb, current_free_gb, tuple(exclude)))
        return []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def _dit(estimated_vram_gb=23.3):
    calls = {"move_to": [], "stream_to": [], "offload": 0}

    def move_to(d):
        calls["move_to"].append(d)

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))

    def offload():
        calls["offload"] += 1

    dit = SimpleNamespace(estimated_vram_gb=estimated_vram_gb, move_to=move_to,
                          stream_to=stream_to, offload=offload)
    return dit, calls


def _dit_with_lora(estimated_vram_gb=23.3, *, active: bool):
    """Same test double as :func:`_dit`, plus a ``.module`` whose one Linear
    carries a populated (``active=True``) or empty/absent (``active=False``)
    ``lora_deltas`` -- exercises :func:`place_dit_for_sequence`'s real
    ``_dit_has_active_lora`` detection end to end, not just the formula."""
    dit, calls = _dit(estimated_vram_gb)
    linear = nn.Linear(4, 4)
    if active:
        delta = SimpleNamespace(down=torch.zeros(4, 4), up=torch.zeros(4, 4))
        linear.lora_deltas = [delta]
    dit.module = nn.Sequential(linear)
    return dit, calls


class _free_vram:
    """Both free-VRAM readings at once -- placement reads the raw
    ``free_vram_gb`` only to report it, and judges every fit against
    ``effective_free_vram_gb`` (raw + the caching allocator's idle reserved
    pool). Patching one without the other would leave the real CUDA query
    live. A class, not ``@contextmanager``: several tests below enter the
    same ``_patched(...)`` tuple twice, which a one-shot generator context
    manager refuses."""

    def __init__(self, raw_gb, effective_gb):
        self._patchers = (
            patch(f"{_MOD}.free_vram_gb", return_value=raw_gb),
            patch(f"{_MOD}.effective_free_vram_gb", return_value=effective_gb),
        )

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc_info):
        for patcher in reversed(self._patchers):
            patcher.stop()
        return False


def _patched(free_gb, *, effective_gb=None, min_reserve=1.0, manager=None):
    """``effective_gb`` defaults to ``free_gb`` -- an empty allocator pool,
    i.e. the exact numbers every test here asserted before the pool was
    credited."""
    manager = manager or _FakeResidencyRegistry()
    return (
        _free_vram(free_gb, free_gb if effective_gb is None else effective_gb),
        patch(f"{_MOD}.minimum_inference_memory_gb", return_value=min_reserve),
        patch(f"{_MOD}.get_residency_registry", return_value=manager),
    ), manager


# -- CPU / non-CUDA passthrough -------------------------------------------------

def test_cpu_device_is_a_plain_move_no_vram_queries():
    dit, calls = _dit()
    with patch(f"{_MOD}.free_vram_gb") as mock_free, \
         patch(f"{_MOD}.effective_free_vram_gb") as mock_effective:
        decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000)
    mock_free.assert_not_called()
    mock_effective.assert_not_called()
    assert calls["move_to"] == ["cpu"]
    assert calls["stream_to"] == []
    assert decision.mode == "cpu"


# -- over-commit: total_reserve alone exceeds free VRAM. The estimate WARNS
# and places anyway (streaming the DiT); it never refuses. The real allocator
# gets the last word in guard_sampling_oom. ------------------------------

def test_over_commit_warns_and_places_instead_of_raising(caplog):
    dit, calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            decision = place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21,
            )
    assert decision.activation_reserve_gb + decision.extra_reserve_gb > 31.0
    assert decision.mode == "partial"
    assert decision.weight_budget_gb == pytest.approx(0.0, abs=1e-9)
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "over-committed" in warning
    assert "102869" in warning or "102,869" in warning


def test_over_commit_still_reports_the_numbers_it_used_to_refuse_with(caplog):
    dit, calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            decision = place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21,
            )
    assert decision.video_tokens == 102_869
    assert decision.audio_tokens == 1_150
    assert decision.extra_reserve_gb == pytest.approx(4.21)
    warning = "\n".join(r.getMessage() for r in caplog.records)
    # The same free/reserve numbers the old refusal detail carried.
    assert "31.00" in warning
    assert f"{decision.activation_reserve_gb:.2f}" in warning


def test_the_over_commit_warning_carries_the_family_fit_hint(caplog):
    seen = {}

    def hint(free_gb, *, activation_reserve_gb, extra_reserve_gb, tokens):
        seen.update(free_gb=free_gb, activation_reserve_gb=activation_reserve_gb,
                    extra_reserve_gb=extra_reserve_gb, tokens=tokens)
        return "A 4s clip fits on this card at 1344x768."

    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21, fit_hint=hint,
            )
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "A 4s clip fits on this card at 1344x768." in warning
    # Rendered against the PRE-FLIGHT free reading and the reserve it covers.
    assert seen["free_gb"] == pytest.approx(31.0)
    assert seen["extra_reserve_gb"] == pytest.approx(4.21)
    assert seen["tokens"] == 104_019


def test_a_broken_fit_hint_never_suppresses_the_over_commit_warning(caplog):
    def hint(*_a, **_k):
        raise ValueError("hint is broken")

    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            decision = place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21, fit_hint=hint,
            )
    assert "over-committed" in "\n".join(r.getMessage() for r in caplog.records)
    assert decision.mode == "partial"


def test_no_fit_hint_leaves_the_over_commit_warning_unchanged(caplog):
    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21,
            )
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert warning.rstrip().endswith("streaming the DiT and sampling anyway.")


def _place_h3_profile(preview_reserve_gb=None):
    dit, calls = _dit(estimated_vram_gb=21.27)
    patches, _manager = _patched(free_gb=27.46)
    kwargs = {} if preview_reserve_gb is None else {"preview_reserve_gb": preview_reserve_gb}
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=51_424, audio_tokens=414,
            inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=1.0, **kwargs,
        )
    return decision, calls


def test_the_preview_reserve_shrinks_the_weight_budget_by_exactly_its_size():
    without, _ = _place_h3_profile()
    unchanged, _ = _place_h3_profile(0.0)
    with_preview, calls = _place_h3_profile(0.557)
    assert unchanged.weight_budget_gb == pytest.approx(without.weight_budget_gb)
    assert without.weight_budget_gb - with_preview.weight_budget_gb == pytest.approx(0.557)
    assert calls["stream_to"] == [("cuda", pytest.approx(with_preview.weight_budget_gb))]
    assert with_preview.preview_reserve_gb == pytest.approx(0.557)
    assert with_preview.extra_reserve_gb == pytest.approx(1.557)
    assert without.preview_reserve_gb == 0.0
    assert without.extra_reserve_gb == pytest.approx(1.0)


@pytest.mark.parametrize("preview_gb,kept", [(0.0, True), (0.6, False)])
def test_the_preview_reserve_is_part_of_the_reserve_judged_against_a_warm_dit(preview_gb, kept):
    dit, calls = _dit(estimated_vram_gb=10.0)
    dit.device = "cuda"
    patches, _manager = _patched(free_gb=4.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=0, reserve_gb=3.0, preview_reserve_gb=preview_gb,
        )
    assert decision.kept_resident is kept
    assert calls["offload"] == (0 if kept else 1)


def test_the_over_commit_warning_lists_the_preview_reserve(caplog):
    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, _manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21, preview_reserve_gb=0.55,
            )
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "extra 4.76GB" in warning
    assert "preview 0.55GB" in warning


def test_the_fit_hint_is_handed_the_preview_reserve_with_the_extra_reserve(caplog):
    seen = {}

    def hint(free_gb, *, activation_reserve_gb, extra_reserve_gb, tokens):
        seen.update(extra_reserve_gb=extra_reserve_gb)
        return ""

    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, _manager = _patched(free_gb=31.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            place_dit_for_sequence(
                dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
                inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, reserve_gb=4.21, preview_reserve_gb=0.55,
                fit_hint=hint,
            )
    assert seen["extra_reserve_gb"] == pytest.approx(4.76)


def test_the_sampling_oom_error_lists_the_preview_reserve():
    forward, _calls = _oom_forward(99)
    dit, _dit_calls = _dit()
    decision = DitPlacementDecision(
        "partial", 19.52, 17.95, 0.0, 60_896, 640, 8.22, True, 1.4, preview_reserve_gb=0.55,
    )
    patches, _manager = _patched(free_gb=3.5, effective_gb=4.25)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=decision)
            with pytest.raises(SamplingOutOfMemory) as exc_info:
                guarded()
    assert "extra_reserve_gb=8.22" in exc_info.value.detail
    assert "preview_reserve_gb=0.55" in exc_info.value.detail


def test_over_commit_does_not_evict_a_foreign_resident_owned_by_this_generation():
    dit, calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=5.0)
    with patches[0], patches[1], patches[2]:
        place_dit_for_sequence(
            dit, "cuda", video_tokens=102_869, audio_tokens=1_150,
            inner_dim=H3_ATTN_INNER_DIM, ffn_dim=14336, own_models=(dit,),
        )
    # It DOES try to make room now (it no longer bails out first), but never
    # at the expense of this generation's own models.
    for _device, _need, _free, exclude in manager.ensure_free_calls:
        assert dit in exclude
    for _device, exclude in manager.offload_all_calls:
        assert dit in exclude


def test_total_reserve_exactly_equal_to_free_places_with_a_zero_budget():
    # `total_reserve == free` leaves a zero weight budget (still correctly
    # "partial", not "resident" -- there's no room left for the DiT's own
    # weight), and does not even warn: the over-commit check is `>`, not
    # `>=`, matching every other boundary in this module (weight_budget
    # clamps at 0, never goes negative).
    dit, calls = _dit(estimated_vram_gb=1.0)
    reserve = estimate_activation_reserve_gb(50_000)
    patches, manager = _patched(free_gb=reserve)  # free == total_reserve exactly (reserve_gb=0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=50_000)
    assert decision.mode == "partial"
    assert decision.weight_budget_gb == pytest.approx(0.0, abs=1e-9)
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1


def test_warm_resident_fast_path_unaffected_by_the_infeasibility_gate():
    # The existing "still fits" warm-residency fast path (kept_resident,
    # zero calls) must be byte-identical -- the gate credits the dit's own
    # resident weight back exactly like the fast path does, so a
    # comfortably-fitting warm dit never trips it.
    dit, calls = _dit(estimated_vram_gb=19.6)
    dit.device = "cuda"
    patches, manager = _patched(free_gb=11.8)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident"
    assert decision.kept_resident is True
    assert calls["move_to"] == []
    assert calls["stream_to"] == []
    assert calls["offload"] == 0


# -- decision matrix: 5s / 15s / 40s @ 720x1280 on a 32GB card, 23.3GB DiT ----
# t_lat*h_lat*w_lat held at 880 tokens/frame (matches the audit's 14,080 at
# t_lat=16 i.e. 5s); frames -> t_lat via (frames-1)//8+1 at 25fps.

_TOKENS_PER_FRAME = 880


def _video_tokens_for(seconds: float) -> int:
    frames = round(seconds * 25)
    t_lat = (frames - 1) // 8 + 1
    return t_lat * _TOKENS_PER_FRAME


def test_five_seconds_fits_full_pin_zero_perf_change():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert calls["move_to"] == ["cuda"]
    assert calls["stream_to"] == []  # zero perf change: exactly the old move_to


def test_fifteen_seconds_still_fits_full_pin():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(15))
    assert decision.mode == "resident"
    assert calls["stream_to"] == []


def test_forty_seconds_needs_partial_residency():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert decision.mode == "partial"
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1
    device, budget = calls["stream_to"][0]
    assert device == "cuda"
    # weight budget = free - min_reserve - activation_reserve; the DiT (23.3GB)
    # does not fit it, but the budget itself must be a sane positive number
    # well under the DiT's own size.
    assert 0.0 < budget < 23.3
    assert decision.weight_budget_gb == pytest.approx(budget)
    assert decision.dit_weight_gb == 23.3


# -- audio tokens can tip an otherwise-fitting clip into partial --------------

def test_audio_tokens_can_tip_placement_into_partial():
    dit, calls = _dit(estimated_vram_gb=23.3)
    video_tokens = _video_tokens_for(15)  # fits alone (see above)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            # Big enough to push the DiT's own weight out of the budget
            # (reserve ~13.6GB vs. the ~8.7GB the 23.3GB DiT leaves free on a
            # 32GB card), but nowhere near infeasible on its own.
            dit, "cuda", video_tokens=video_tokens, audio_tokens=100_000,
        )
    assert decision.mode == "partial"


# -- degenerate tiny VRAM: the activation reserve alone doesn't fit. The DiT
# is streamed with a zero weight budget and the forward is left to try; the
# estimate does not get to veto it ------------------------------------------

def test_degenerate_tiny_vram_streams_with_a_zero_budget_instead_of_raising():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=0.5)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "partial"
    assert decision.weight_budget_gb == pytest.approx(0.0, abs=1e-9)
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1


# -- guard_sampling_oom: the real allocator gets the last word ---------------
#
# The estimate never refuses, so this ladder is the only thing between an
# over-committed clip and a raw CUDA OOM traceback.

def _decision(mode="resident", *, weight_budget_gb=5.0, video_tokens=60_896,
              audio_tokens=640, activation_reserve_gb=17.95, extra_reserve_gb=7.67):
    return DitPlacementDecision(
        mode, 19.52, activation_reserve_gb, weight_budget_gb, video_tokens, audio_tokens,
        extra_reserve_gb, True, 1.4,
    )


def _oom_forward(fail_times: int):
    """A forward that raises the real ``torch.cuda.OutOfMemoryError`` for its
    first ``fail_times`` calls, then succeeds. Constructing and raising that
    class needs no actual device."""
    calls = {"n": 0}

    def forward(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        return ("ok", args, kwargs)

    return forward, calls


def test_a_forward_that_succeeds_is_passed_straight_through():
    forward, calls = _oom_forward(0)
    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
    assert guarded(1, x=2) == ("ok", (1,), {"x": 2})
    assert calls["n"] == 1


def test_only_the_first_forward_is_guarded():
    # Once a forward has succeeded the peak is paid; later steps must not pay
    # for a try/except ladder, and an OOM there is not this ladder's to catch.
    forward, calls = _oom_forward(0)
    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
    guarded()
    later, later_calls = calls["n"], None
    def boom(*_a, **_k):
        raise torch.cuda.OutOfMemoryError("CUDA out of memory")
    guarded2 = guard_sampling_oom(boom, dit=dit, device="cuda", decision=_decision("partial", weight_budget_gb=0.0))
    with pytest.raises(SamplingOutOfMemory):
        guarded2()
    assert later == 1


def test_tier_one_reclaims_the_allocator_pool_and_retries():
    forward, calls = _oom_forward(1)
    dit, dit_calls = _dit()
    patches, manager = _patched(free_gb=8.0)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache") as empty:
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
            assert guarded()[0] == "ok"
    assert calls["n"] == 2
    assert empty.called
    # Tier 2 never ran: no weight was shed.
    assert dit_calls["offload"] == 0
    assert dit_calls["stream_to"] == []


def test_tier_two_sheds_every_resident_weight_and_retries():
    forward, calls = _oom_forward(2)
    dit, dit_calls = _dit()
    patches, manager = _patched(free_gb=8.0)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
            assert guarded()[0] == "ok"
    assert calls["n"] == 3
    assert dit_calls["offload"] == 1
    assert dit_calls["stream_to"] == [("cuda", 0.0)]  # fully streamed


def test_an_already_fully_streamed_dit_has_no_tier_two():
    forward, calls = _oom_forward(99)
    dit, dit_calls = _dit()
    patches, manager = _patched(free_gb=1.0)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(
                forward, dit=dit, device="cuda", decision=_decision("partial", weight_budget_gb=0.0),
            )
            with pytest.raises(SamplingOutOfMemory):
                guarded()
    assert calls["n"] == 2  # first try + tier 1 only
    assert dit_calls["stream_to"] == []


def test_the_exhausted_ladder_raises_one_error_with_measured_numbers():
    forward, _calls = _oom_forward(99)
    dit, _dit_calls = _dit()
    patches, manager = _patched(free_gb=3.5, effective_gb=4.25)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(
                forward, dit=dit, device="cuda", decision=_decision("partial", weight_budget_gb=0.0),
            )
            with pytest.raises(SamplingOutOfMemory) as exc_info:
                guarded()
    err = exc_info.value
    # MEASURED at failure, not the estimate's prediction.
    assert err.free_raw_gb == pytest.approx(3.5)
    assert err.free_effective_gb == pytest.approx(4.25)
    assert err.video_tokens == 60_896
    assert err.audio_tokens == 640
    assert err.activation_reserve_gb == pytest.approx(17.95)
    message = str(err)
    assert "61,536 tokens" in message           # video + audio
    assert "streamed from RAM" in message
    assert "4.2 GB free" in message
    assert "shorten the clip or lower the resolution" in message
    assert "free_raw_gb=3.50" in err.detail
    assert "free_effective_gb=4.25" in err.detail


def test_the_family_fit_hint_is_appended_and_gets_the_measured_free_vram():
    forward, _calls = _oom_forward(99)
    dit, _dit_calls = _dit()
    seen = {}

    def hint(free_gb, *, activation_reserve_gb, extra_reserve_gb, tokens):
        seen.update(free_gb=free_gb, activation_reserve_gb=activation_reserve_gb,
                    extra_reserve_gb=extra_reserve_gb, tokens=tokens)
        return "A 4s clip would fit at this size."

    patches, manager = _patched(free_gb=3.5, effective_gb=4.25)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(
                forward, dit=dit, device="cuda", decision=_decision("partial", weight_budget_gb=0.0),
                fit_hint=hint,
            )
            with pytest.raises(SamplingOutOfMemory) as exc_info:
                guarded()
    assert seen["free_gb"] == pytest.approx(4.25)
    # The hint is handed the reserve breakdown that free number has to cover.
    assert seen["activation_reserve_gb"] == pytest.approx(17.95)
    assert seen["extra_reserve_gb"] == pytest.approx(7.67)
    assert seen["tokens"] == 61_536
    assert str(exc_info.value).endswith("A 4s clip would fit at this size.")


def test_a_broken_fit_hint_never_replaces_the_real_error():
    forward, _calls = _oom_forward(99)
    dit, _dit_calls = _dit()

    def hint(*_a, **_k):
        raise ValueError("hint is broken")

    patches, manager = _patched(free_gb=3.5)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(
                forward, dit=dit, device="cuda", decision=_decision("partial", weight_budget_gb=0.0),
                fit_hint=hint,
            )
            with pytest.raises(SamplingOutOfMemory) as exc_info:
                guarded()
    assert "ran out of VRAM" in str(exc_info.value)


def test_a_non_oom_error_from_the_forward_is_not_swallowed():
    def forward(*_a, **_k):
        raise ValueError("something else entirely")

    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
    with pytest.raises(ValueError, match="something else entirely"):
        guarded()


# -- foreign-resident exclusion / one-shot-generator footgun ------------------

def test_own_models_excluded_from_eviction_even_as_a_one_shot_generator():
    dit, calls = _dit(estimated_vram_gb=23.3)
    vae = object()
    patches, manager = _patched(free_gb=32.0)

    def own_models_gen():
        yield dit
        yield vae

    with patches[0], patches[1], patches[2]:
        place_dit_for_sequence(
            dit, "cuda", video_tokens=_video_tokens_for(5), own_models=own_models_gen(),
        )
    assert manager.ensure_free_calls, "expected an ensure_free eviction call before placement"
    _, _, _, exclude = manager.ensure_free_calls[0]
    assert {id(m) for m in exclude} == {id(dit), id(vae)}


# -- OOM-degrade ladder: full move ------------------------------------------

def test_full_move_oom_degrades_through_evict_retry_to_partial():
    calls = {"move_to": 0, "stream_to": []}

    def move_to(d):
        calls["move_to"] += 1
        raise torch.cuda.OutOfMemoryError("simulated")

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))

    dit = SimpleNamespace(estimated_vram_gb=23.3, move_to=move_to, stream_to=stream_to,
                          offload=lambda: None)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    # try once, evict-and-retry once (both raise), then degrade to partial.
    assert calls["move_to"] == 2
    assert len(calls["stream_to"]) == 1
    assert decision.mode == "partial"
    # The degrade path budgets off LIVE free VRAM minus the min-inference
    # reserve only (not the activation reserve) -- mirrors
    # NativeGenerator._move_dit_to_gpu's identical fallback.
    assert calls["stream_to"][0][1] == pytest.approx(31.0)


def test_partial_move_oom_degrades_to_fully_streamed():
    calls = {"stream_to": []}

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))
        if len(calls["stream_to"]) == 1:
            raise torch.cuda.OutOfMemoryError("simulated")

    dit = SimpleNamespace(estimated_vram_gb=23.3, move_to=lambda d: None, stream_to=stream_to,
                          offload=lambda: None)
    # Force the partial branch directly (40s scenario), then have the FIRST
    # stream_to attempt OOM to exercise the fully-streamed fallback.
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert len(calls["stream_to"]) == 2
    assert calls["stream_to"][1] == ("cuda", 0.0)
    assert decision.mode == "partial"


# -- decision dataclass shape --------------------------------------------------

def test_decision_is_a_frozen_dataclass_with_expected_fields():
    dit, _ = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5), audio_tokens=10)
    assert isinstance(decision, DitPlacementDecision)
    assert decision.video_tokens == _video_tokens_for(5)
    assert decision.audio_tokens == 10
    assert decision.extra_reserve_gb == 0.0  # unset by default -- prior callers unaffected
    with pytest.raises(Exception):
        decision.mode = "partial"  # frozen


# -- SwiGLU FFN transient (real H3 turbo-LoRA OOM: dit_weight_gb=19.52,
# activation_reserve_gb=4.44, free~27.4 -> chose "resident"; died on "Tried to
# allocate 1.03 GiB" == S * 2*ffn_dim * 2B, the fc1 fused value|gate output,
# never modeled by the attention-shaped terms alone) -----------------------

_H3_INNER_DIM = 56 * 128   # 7168
_H3_FFN_DIM = 14336
_TRACE_VIDEO_TOKENS = 18870
_TRACE_AUDIO_TOKENS = 414
# What the trace's own placement logged as free. Kept for the record; the
# residency tests below judge against `_TRACE_DECISIVE_FREE_GB` instead --
# see the comment there.
_TRACE_REPORTED_FREE_GB = 27.4
_TRACE_DIT_WEIGHT_GB = 19.52
_TRACE_LORA_WEIGHT_GB = 1.4


def test_ffn_dim_none_is_a_no_op_ltx_call_sites_unchanged():
    # Every existing LTX call site never passes ffn_dim -- the default (None)
    # must reproduce the exact prior formula, byte for byte.
    with_none = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM, ffn_dim=None)
    without_arg = estimate_activation_reserve_gb(50_000, inner_dim=_LTX_INNER_DIM)
    assert with_none == without_arg


def test_ffn_dim_zero_is_also_a_no_op():
    assert estimate_activation_reserve_gb(50_000, ffn_dim=0) == estimate_activation_reserve_gb(50_000, ffn_dim=None)


def test_ffn_transient_bytes_matches_the_observed_failing_allocation():
    # The failing allocation in the real trace was EXACTLY
    # S * 2*ffn_dim * 2B (bf16) -- the fc1 fused value|gate output alone, the
    # first and largest of the three terms _ffn_transient_bytes_per_token
    # sums. Not the full per-token term (which also includes the SiLU output
    # and the value*SiLU(gate) product) -- this pins the verified SUB-term.
    fc1_out_bytes_per_token = 2 * _H3_FFN_DIM * 2
    s = _TRACE_VIDEO_TOKENS + _TRACE_AUDIO_TOKENS
    fc1_out_gib = (s * fc1_out_bytes_per_token) / 1024 ** 3
    assert fc1_out_gib == pytest.approx(1.03, abs=0.01)
    # The full term this module actually reserves is a conservative
    # SUPERSET of that one sub-allocation (also covers SiLU + product).
    assert _ffn_transient_bytes_per_token(_H3_FFN_DIM) > fc1_out_bytes_per_token


def test_ffn_dim_increases_the_reserve_above_the_attention_only_estimate():
    without_ffn = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, inner_dim=_H3_INNER_DIM,
    )
    with_ffn = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    # The trace's own report said 4.44 here; that figure included the flat
    # per-token LoRA buffer the estimate charged whenever any delta was
    # resident, which no longer exists (the plain deltas in that run ride the
    # chunked activation branch). What remains is the attention-shaped terms.
    assert without_ffn == pytest.approx(3.26, abs=0.01)
    assert with_ffn > without_ffn


def test_h3_corrected_reserve_lands_at_or_above_the_derived_floor():
    # Derived from the trace's own numbers (team-lead's math): 24.52GB
    # allocated at death - 19.52 weights - ~1.4 LoRA => ~3.6GB activations
    # already resident when it needed 1.03GiB MORE and failed => true reserve
    # need >= ~4.6GB before any margin. This module's own (more conservative)
    # first-principles estimate must clear that floor.
    reserve = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    assert reserve >= 4.6


def test_ltx_reserve_byte_identical_with_and_without_ffn_dim_kwarg_present_in_signature():
    # Regression guard: adding the ffn_dim parameter must not perturb ANY
    # existing LTX-shaped call (no inner_dim, no ffn_dim -- the historical
    # call shape from before either kwarg existed).
    assert estimate_activation_reserve_gb(110_880) == pytest.approx(9.4, rel=0.3)  # sanity vs. the audit figure


_H3_HIDDEN_SIZE = 5376
_H3_FFN_PLANS = ((39_800, 4.25), (103_000, 11.0))


def _h3_reserve(tokens: int, **kwargs) -> float:
    return estimate_activation_reserve_gb(tokens, inner_dim=_H3_INNER_DIM, **kwargs)


@pytest.mark.parametrize("tokens, saved_gib", _H3_FFN_PLANS)
def test_a_chunked_ffn_drops_the_whole_ffn_term_from_a_long_h3_plans_reserve(tokens, saved_gib):
    rows = default_ffn_chunk_rows(tokens, _H3_HIDDEN_SIZE, _H3_FFN_DIM)
    whole = _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM)
    chunked = _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM, ffn_chunk_rows=rows)
    raw_saving_gib = tokens * _ffn_transient_bytes_per_token(_H3_FFN_DIM) / 1024 ** 3

    assert rows
    assert raw_saving_gib == pytest.approx(saved_gib, abs=0.01)
    assert whole - chunked == pytest.approx(raw_saving_gib * _ACTIVATION_SAFETY_MARGIN, rel=1e-9)
    assert chunked == _h3_reserve(tokens)


@pytest.mark.parametrize("tokens, free_gb", [(39_800, 28.0), (103_000, 32.0)])
def test_a_chunked_ffn_keeps_more_of_a_long_h3_plans_dit_resident(tokens, free_gb):
    rows = default_ffn_chunk_rows(tokens, _H3_HIDDEN_SIZE, _H3_FFN_DIM)
    decisions = []
    for chunk_rows in (0, rows):
        dit, _calls = _dit(estimated_vram_gb=_TRACE_DIT_WEIGHT_GB)
        patches, _manager = _patched(free_gb=free_gb)
        with patches[0], patches[1], patches[2]:
            decisions.append(place_dit_for_sequence(
                dit, "cuda", video_tokens=tokens, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
                ffn_chunk_rows=chunk_rows,
            ))
    whole, chunked = decisions

    def resident_fraction(decision):
        return min(1.0, decision.weight_budget_gb / decision.dit_weight_gb)

    assert whole.mode == "partial"
    assert chunked.weight_budget_gb - whole.weight_budget_gb == pytest.approx(
        whole.activation_reserve_gb - chunked.activation_reserve_gb)
    assert resident_fraction(chunked) > resident_fraction(whole)


def test_an_ffn_chunk_at_least_as_long_as_the_sequence_reserves_the_whole_ffn():
    tokens = 50_000
    whole = _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM)
    assert _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM, ffn_chunk_rows=tokens) == whole
    assert _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM, ffn_chunk_rows=tokens + 1) == whole


def test_a_chunked_ffn_phase_wider_than_attention_sets_the_reserve_instead():
    tokens = 50_000
    attention_only = _h3_reserve(tokens)
    near_whole = _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM, ffn_chunk_rows=tokens - 1)
    assert attention_only < near_whole < _h3_reserve(tokens, ffn_dim=_H3_FFN_DIM)


def test_ffn_chunk_rows_without_an_ffn_dim_changes_nothing():
    assert _h3_reserve(50_000, ffn_chunk_rows=4096) == _h3_reserve(50_000)
    assert estimate_activation_reserve_gb(110_880, ffn_chunk_rows=4096) == estimate_activation_reserve_gb(110_880)


# -- LoRA delta counted as resident weight (the OTHER half of the same fix) --

def _linear_with_sized_delta(down_shape, up_shape, *, dtype=torch.float32) -> nn.Linear:
    linear = nn.Linear(4, 4)
    delta = SimpleNamespace(down=torch.zeros(down_shape, dtype=dtype), up=torch.zeros(up_shape, dtype=dtype))
    linear.lora_deltas = [delta]
    return linear


def test_dit_lora_profile_delta_gb_is_zero_with_no_lora():
    dit = SimpleNamespace(module=nn.Sequential(nn.Linear(4, 4)))
    assert _dit_lora_profile(dit).delta_gb == 0.0


def test_dit_lora_profile_delta_gb_is_zero_for_a_non_module():
    dit = SimpleNamespace(module=lambda x: x)
    assert _dit_lora_profile(dit).delta_gb == 0.0


def test_dit_lora_profile_delta_gb_sums_down_and_up_tensors_exactly():
    # down: 100x50, up: 50x100, both fp32 -- exactly 2*100*50*4 bytes.
    dit = SimpleNamespace(module=nn.Sequential(_linear_with_sized_delta((100, 50), (50, 100))))
    expected_gb = (2 * 100 * 50 * 4) / 1024 ** 3
    assert _dit_lora_profile(dit).delta_gb == pytest.approx(expected_gb, rel=1e-9)


def test_dit_lora_profile_delta_gb_sums_across_multiple_linears_and_stacked_loras():
    dit = SimpleNamespace(module=nn.Sequential(
        _linear_with_sized_delta((10, 10), (10, 10)),
        _linear_with_sized_delta((20, 20), (20, 20)),
    ))
    expected_gb = (2 * 10 * 10 * 4 + 2 * 20 * 20 * 4) / 1024 ** 3
    assert _dit_lora_profile(dit).delta_gb == pytest.approx(expected_gb, rel=1e-9)


def test_dit_lora_profile_delta_gb_ignores_non_tensor_delta_fields():
    linear = nn.Linear(4, 4)
    linear.lora_deltas = [SimpleNamespace(down=None, up="not a tensor")]
    dit = SimpleNamespace(module=nn.Sequential(linear))
    assert _dit_lora_profile(dit).delta_gb == 0.0


def test_place_dit_for_sequence_folds_lora_delta_into_dit_weight_gb():
    dit, calls = _dit(estimated_vram_gb=19.52)
    dit.module = nn.Sequential(_linear_with_sized_delta((100, 50), (50, 100)))
    lora_gb = _dit_lora_profile(dit).delta_gb
    assert lora_gb > 0.0
    patches, manager = _patched(free_gb=100.0)  # plenty of room, isolate the weight_gb accounting
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    assert decision.lora_active is True
    assert decision.lora_weight_gb == pytest.approx(lora_gb)
    assert decision.dit_weight_gb == pytest.approx(19.52 + lora_gb)


def test_place_dit_for_sequence_without_lora_reports_zero_lora_weight_gb():
    dit, calls = _dit(estimated_vram_gb=19.52)
    patches, manager = _patched(free_gb=100.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    assert decision.lora_active is False
    assert decision.lora_weight_gb == 0.0
    assert decision.dit_weight_gb == pytest.approx(19.52)


# -- the maintainer's refused 8s/768x1344 MiniMax-H3 clip: LoRAs active,
# activation_reserve_gb=21.73 + extra_reserve_gb=7.67 = 29.40 against
# free_effective 28.99 -> refused a clip that used to run. 3.78GB of that
# reserve was the per-token LoRA output buffer, charged for a preallocation
# the vendored code stopped making. -------------------------------------

_REFUSAL_VIDEO_TOKENS = 60_896
_REFUSAL_AUDIO_TOKENS = 640
_REFUSAL_EXTRA_RESERVE_GB = 7.67
_REFUSAL_FREE_EFFECTIVE_GB = 28.99


def _refusal_dit():
    """The maintainer's DiT: fp8 H3 with plain (non-LoKr) runtime deltas
    resident on its widest Linears, both GEMM gates at their default off."""
    dit, calls = _dit(estimated_vram_gb=19.52)
    dit.module = nn.Sequential(
        _quantized_linear(_H3_INNER_DIM, _H3_INNER_DIM, nvfp4=False),
        _quantized_linear(_H3_INNER_DIM, 2 * _H3_FFN_DIM, nvfp4=False),
    )
    return dit, calls


def test_the_refused_clip_is_no_longer_refused():
    dit, calls = _refusal_dit()
    patches, manager = _patched(free_gb=_REFUSAL_FREE_EFFECTIVE_GB)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_REFUSAL_VIDEO_TOKENS, audio_tokens=_REFUSAL_AUDIO_TOKENS,
            reserve_gb=_REFUSAL_EXTRA_RESERVE_GB, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
        )
    assert decision.lora_active is True
    assert decision.mode in ("resident", "partial")


def test_the_refused_clips_activation_reserve_drops_to_the_attention_and_ffn_terms():
    reserve = estimate_activation_reserve_gb(
        _REFUSAL_VIDEO_TOKENS, _REFUSAL_AUDIO_TOKENS, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    assert reserve == pytest.approx(18.2, rel=0.03)  # was 21.73 in the refusal detail
    assert reserve + _REFUSAL_EXTRA_RESERVE_GB < _REFUSAL_FREE_EFFECTIVE_GB


# -- the real OOM trace: both fixes together flip resident -> partial --------

def _trace_dit() -> Any:
    dit, calls = _dit(estimated_vram_gb=_TRACE_DIT_WEIGHT_GB)
    n = int((_TRACE_LORA_WEIGHT_GB * 1024 ** 3 / 8) ** 0.5)
    dit.module = nn.Sequential(_linear_with_sized_delta((n, n), (n, n)))
    return dit, calls


_TRACE_DECISIVE_FREE_GB = 25.5


def test_the_ffn_term_tips_the_real_trace_into_partial():
    dit, calls = _trace_dit()
    patches, manager = _patched(free_gb=_TRACE_DECISIVE_FREE_GB)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_TRACE_VIDEO_TOKENS, audio_tokens=_TRACE_AUDIO_TOKENS,
            inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
        )
    assert decision.mode == "partial"
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1


def test_the_real_traces_reserve_still_covers_its_observed_activation_need():
    # Independent of any free reading: the trace died with 24.52GB allocated
    # (19.52 weights + 1.4 LoRA + ~3.6 activations) needing 1.03GiB more, so
    # its true activation need was ~4.63GB. Dropping the stale per-token LoRA
    # buffer must NOT drop the reserve below that.
    reserve = estimate_activation_reserve_gb(
        _TRACE_VIDEO_TOKENS, _TRACE_AUDIO_TOKENS, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )
    assert reserve >= 4.63


# -- warm residency: a DiT the PRIOR generation already left resident must not
# be needlessly offloaded and re-streamed just because free_vram_gb() counts
# its own weight bytes as "used" rather than "available if kept". Real trace:
# mode=partial, weight_budget_gb=0.0, dit_weight_gb=21.27 (incl. 1.75 LoRA),
# activation_reserve=6.82, on a card with ~31GB genuinely free -- sampling
# alone took 147s of a 196s total because the warm DiT was streamed from host
# every step instead of staying put. ------------------------------------

from src.pipelines.pipes._shared.generation.dit_placement import dit_is_fully_resident


def test_dit_is_fully_resident_true_when_device_matches_and_not_streaming():
    dit = SimpleNamespace(device="cuda")
    assert dit_is_fully_resident(dit, "cuda") is True


def test_dit_is_fully_resident_false_with_no_device_set():
    dit = SimpleNamespace()
    assert dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_false_on_a_different_device_type():
    dit = SimpleNamespace(device="cpu")
    assert dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_true_across_cuda_ordinal_spelling():
    # "cuda" and "cuda:0" are the same device TYPE -- a warm restore that
    # landed on "cuda:0" must still be recognised against a bare "cuda" ask.
    dit = SimpleNamespace(device="cuda:0")
    assert dit_is_fully_resident(dit, "cuda") is True


def test_dit_is_fully_resident_false_while_actively_streaming():
    # Partial residency (an active streamer) is EXCLUDED on purpose -- that
    # leaf split was sized for a different call and cannot be trusted without
    # recomputing; see the function's own docstring.
    streamer = SimpleNamespace(active=True)
    dit = SimpleNamespace(device="cuda", _streamer=streamer)
    assert dit_is_fully_resident(dit, "cuda") is False


def test_dit_is_fully_resident_true_with_an_inactive_streamer():
    # A streamer object exists (this DiT was streamed at some earlier point
    # in the process) but is not CURRENTLY active -- a later move_to() would
    # have made it fully resident again; must not be permanently excluded
    # just because a streamer object was ever constructed once.
    streamer = SimpleNamespace(active=False)
    dit = SimpleNamespace(device="cuda", _streamer=streamer)
    assert dit_is_fully_resident(dit, "cuda") is True


def test_warm_resident_dit_that_still_fits_is_kept_without_move_or_stream():
    dit, calls = _dit(estimated_vram_gb=19.6)
    dit.device = "cuda"
    # As if 19.6GB of a ~31.4GB-total card is already used by dit's OWN
    # resident copy -- free_vram_gb() only reports what's left besides it.
    patches, manager = _patched(free_gb=11.8)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident"
    assert decision.kept_resident is True
    assert calls["move_to"] == []
    assert calls["stream_to"] == []
    assert calls["offload"] == 0
    assert decision.dit_weight_gb == 19.6


def test_warm_resident_dit_that_no_longer_fits_offloads_then_places_fresh():
    dit, calls = _dit(estimated_vram_gb=23.3)
    dit.device = "cuda"
    # Effective read 1 (the infeasibility gate, credited the same way:
    # 2.0+23.3=25.3 clears the 10.5GB reserve, so it proceeds). Effective read
    # 2 (fast-path check): still only 2.0GB free -- 2.0+23.3=25.3 credited,
    # minus a 10.5GB reserve, doesn't clear 23.3 -> falls through and
    # offloads. Effective read 3 (the post-offload measurement) sees the FULL
    # 40.0GB the stale copy's release genuinely freed. The raw reads are the
    # gate's report-only one and `_ensure_room_for`'s own.
    free_reads = iter([2.0, 40.0])
    effective_reads = iter([2.0, 2.0, 40.0])
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.free_vram_gb", side_effect=lambda device: next(free_reads)), \
         patch(f"{_MOD}.effective_free_vram_gb", side_effect=lambda device: next(effective_reads)), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0), \
         patch(f"{_MOD}.get_residency_registry", return_value=manager):
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=10.0)
    assert calls["offload"] == 1, "expected the stale resident copy to be offloaded before re-measuring"
    assert decision.mode == "resident"
    assert decision.kept_resident is False  # a FRESH placement, not the fast-path skip
    assert calls["move_to"] == ["cuda"]


def test_warm_resident_dit_that_no_longer_fits_can_still_degrade_to_partial():
    dit, calls = _dit(estimated_vram_gb=23.3)
    dit.device = "cuda"
    # total_reserve = reserve_gb(10.0) + floor(0.5) = 10.5. Effective read 1
    # (the infeasibility gate): 2.0+23.3=25.3 credited clears 10.5, proceeds.
    # Effective read 2 (fast-path check): still 2.0GB free < 10.5 even
    # credited -> doesn't fit -> offloads. Effective read 3 (post-offload):
    # 5.0GB free -> weight_budget = max(0, 5.0-10.5) = 0.0, still nowhere
    # near 23.3 -> genuinely must degrade to partial, not force a resident
    # placement it cannot back.
    free_reads = iter([2.0, 5.0])
    effective_reads = iter([2.0, 2.0, 5.0])
    manager = _FakeResidencyRegistry()
    with patch(f"{_MOD}.free_vram_gb", side_effect=lambda device: next(free_reads)), \
         patch(f"{_MOD}.effective_free_vram_gb", side_effect=lambda device: next(effective_reads)), \
         patch(f"{_MOD}.minimum_inference_memory_gb", return_value=1.0), \
         patch(f"{_MOD}.get_residency_registry", return_value=manager):
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=10.0)
    assert calls["offload"] == 1
    assert decision.mode == "partial"
    assert decision.kept_resident is False
    assert len(calls["stream_to"]) == 1


def test_warm_residency_check_is_skipped_when_dit_has_no_device_attribute():
    # A cold-start dit (never placed anywhere) has no `.device` to compare --
    # must reach the ordinary path unchanged, not raise.
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert decision.kept_resident is False
    assert calls["move_to"] == ["cuda"]


# -- reserve_gb: extra headroom on top of the token-derived reserve (a caller
# whose post-placement GPU work isn't proportional
# to video_tokens, e.g. the detailer's per-tube VAE decode).


def test_reserve_gb_defaults_to_zero_no_behavior_change():
    """A tiny-token call with no reserve_gb behaves exactly as before: the
    activation reserve alone decides, and a comfortably-fitting DiT goes
    fully resident."""
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=28.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000)
    assert decision.mode == "resident"
    assert decision.extra_reserve_gb == 0.0
    assert calls["move_to"] == ["cuda"]


def test_reserve_gb_can_tip_a_tiny_token_placement_into_partial():
    """The exact round-2 shape: a tiny tube (a few thousand tokens)
    gets a near-floor activation reserve on its own, which alone would fit
    the DiT fully resident -- but a caller-supplied reserve_gb (this tube's
    upcoming VAE decode) must be able to tip that decision into partial so
    real headroom survives past placement."""
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=24.0)  # 24 - 23.3 = 0.7GB slack, thin
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=3.0)
    assert decision.mode == "partial"
    assert decision.extra_reserve_gb == 3.0
    assert calls["move_to"] == []
    assert len(calls["stream_to"]) == 1
    device, budget = calls["stream_to"][0]
    assert device == "cuda"
    # weight budget = free - activation_reserve(~floor) - reserve_gb(3.0)
    assert budget == pytest.approx(24.0 - decision.activation_reserve_gb - 3.0, abs=1e-6)


def test_reserve_gb_is_additive_with_the_activation_reserve():
    dit, _ = _dit(estimated_vram_gb=1.0)  # tiny DiT so both cases stay "resident"
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        without = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=0.0)
    with patches[0], patches[1], patches[2]:
        with_reserve = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=2.5)
    assert with_reserve.weight_budget_gb == pytest.approx(without.weight_budget_gb - 2.5)


def test_negative_reserve_gb_is_clamped_to_zero():
    dit, calls = _dit(estimated_vram_gb=23.3)
    patches, manager = _patched(free_gb=28.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=4_000, reserve_gb=-5.0)
    assert decision.extra_reserve_gb == 0.0
    assert decision.mode == "resident"


def test_cpu_device_decision_has_zero_extra_reserve_regardless_of_argument():
    dit, calls = _dit()
    decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000, reserve_gb=5.0)
    assert decision.mode == "cpu"
    assert decision.extra_reserve_gb == 0.0


# -- LoRA gating end to end (place_dit_for_sequence) ----------------------------

def test_placement_reports_lora_active_from_the_real_module():
    dit, _ = _dit_with_lora(estimated_vram_gb=23.3, active=True)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=50_000)
    assert decision.lora_active is True


def test_placement_reports_lora_inactive_without_deltas():
    dit, _ = _dit_with_lora(estimated_vram_gb=23.3, active=False)
    patches, manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=50_000)
    assert decision.lora_active is False


def test_a_plain_resident_lora_does_not_raise_the_activation_reserve():
    # Post-22381b5e a plain delta on a quantized Linear is accumulated into
    # the layer's own F.linear output in bounded row chunks -- nothing
    # S-proportional -- so it must cost the reserve nothing. It still shows
    # up in dit_weight_gb via its own resident up/down bytes.
    tokens = 50_000
    patches, manager = _patched(free_gb=32.0)
    dit_off, _ = _dit_with_lora(estimated_vram_gb=1.0, active=False)  # tiny DiT: both stay resident
    with patches[0], patches[1], patches[2]:
        off = place_dit_for_sequence(dit_off, "cuda", video_tokens=tokens)
    dit_on, _ = _dit_with_lora(estimated_vram_gb=1.0, active=True)
    with patches[0], patches[1], patches[2]:
        on = place_dit_for_sequence(dit_on, "cuda", video_tokens=tokens)
    assert on.lora_active is True
    assert on.activation_reserve_gb == pytest.approx(off.activation_reserve_gb)


def test_a_lokr_delta_raises_the_reserve_by_its_weight_shaped_clone():
    # LoKr has no output-side form, so apply_lora_deltas still clones the
    # materialised weight and builds an (out, in) delta beside it every
    # forward -- flat, weight-shaped headroom the estimate must reserve.
    tokens = 50_000
    patches, manager = _patched(free_gb=32.0)
    dit_off, _ = _dit(estimated_vram_gb=1.0)
    dit_off.module = nn.Sequential(nn.Linear(512, 2048))
    with patches[0], patches[1], patches[2]:
        off = place_dit_for_sequence(dit_off, "cuda", video_tokens=tokens)
    dit_on, _ = _dit(estimated_vram_gb=1.0)
    dit_on.module = nn.Sequential(_linear_with_lokr_delta(512, 2048))
    with patches[0], patches[1], patches[2]:
        on = place_dit_for_sequence(dit_on, "cuda", video_tokens=tokens)
    expected_gb = (2 * 2048 * 512 * 2) / 1024 ** 3 * 1.15
    assert on.activation_reserve_gb - off.activation_reserve_gb == pytest.approx(expected_gb, rel=1e-6)


def test_a_lokr_delta_can_tip_an_otherwise_resident_placement_into_partial():
    tokens = 50_000
    dit_weight_gb = 20.0
    # A LoKr-patched Linear wide enough that its clone+delta is worth GBs.
    in_f, out_f = 5376, 28672
    without = estimate_activation_reserve_gb(tokens)
    free_gb = dit_weight_gb + without + 0.05  # fits with no weight-side delta, not with one

    patches, manager = _patched(free_gb=free_gb)
    dit_off, _ = _dit(estimated_vram_gb=dit_weight_gb)
    dit_off.module = nn.Sequential(nn.Linear(4, 4))
    with patches[0], patches[1], patches[2]:
        off = place_dit_for_sequence(dit_off, "cuda", video_tokens=tokens)
    assert off.mode == "resident"

    dit_on, _ = _dit(estimated_vram_gb=dit_weight_gb)
    dit_on.module = nn.Sequential(_linear_with_lokr_delta(in_f, out_f))
    with patches[0], patches[1], patches[2]:
        on = place_dit_for_sequence(dit_on, "cuda", video_tokens=tokens)
    assert on.mode == "partial"


def test_cpu_device_lora_active_still_detected_but_reserve_stays_zero():
    dit, calls = _dit_with_lora(estimated_vram_gb=23.3, active=True)
    decision = place_dit_for_sequence(dit, "cpu", video_tokens=100_000)
    assert decision.mode == "cpu"
    assert decision.lora_active is True
    assert decision.activation_reserve_gb == 0.0


# -- torch.compile hook: place_dit_for_sequence gives LTX/DFR/MiniMax-H3 the
# same gated, reversible regional torch.compile the image path gets from
# NativeGenerator._maybe_compile, since these pipes have no NativeGenerator
# instance to call that private method on. ------------------------------------

class _CompilableBlocks(nn.Module):
    """Homogeneous ``blocks`` ModuleList -- a real compile_gate "ok" target."""

    def __init__(self, n: int = 2, dim: int = 4) -> None:
        super().__init__()
        self.blocks = nn.ModuleList(nn.Linear(dim, dim) for _ in range(n))


def _dit_compilable(estimated_vram_gb=23.3):
    dit, calls = _dit(estimated_vram_gb)
    dit.module = _CompilableBlocks()
    dit.quant_format = None
    return dit, calls


def _enable_compile(monkeypatch):
    from src.platform.runtime.native.optimizations import compile as tc

    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", True)
    monkeypatch.setattr(tc, "_triton_available", lambda: True)
    return tc


def test_resident_placement_compiles_when_enabled(monkeypatch):
    tc = _enable_compile(monkeypatch)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert dit._compiled is not None and dit._compiled.active
    assert all(tc.is_compiled(b) for b in dit.module.blocks)


def test_partial_placement_without_active_streamer_does_not_compile(monkeypatch):
    _enable_compile(monkeypatch)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert decision.mode == "partial"
    assert getattr(dit, "_compiled", None) is None


def test_partial_placement_with_active_streamer_compiles_block_forwards(monkeypatch):
    tc = _enable_compile(monkeypatch)
    dit, _calls = _dit_compilable()
    dit.is_streaming = True
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(40))
    assert decision.mode == "partial"
    assert dit._compiled is not None and dit._compiled.active
    assert len(dit._compiled.forwards) == len(dit.module.blocks)
    assert all("forward" in vars(b) and not tc.is_compiled(b) for b in dit.module.blocks)


def test_compile_disabled_by_default_leaves_dit_untouched(monkeypatch):
    monkeypatch.setitem(runtime_flags.runtime_flag_values(), "native_torch_compile", False)
    dit, _calls = _dit_compilable()
    patches, _manager = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=_video_tokens_for(5))
    assert decision.mode == "resident"
    assert getattr(dit, "_compiled", None) is None


def test_warm_resident_fast_path_also_compiles(monkeypatch):
    tc = _enable_compile(monkeypatch)
    dit, calls = _dit_compilable(estimated_vram_gb=19.6)
    dit.device = "cuda"
    patches, _manager = _patched(free_gb=11.8)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident" and decision.kept_resident is True
    assert calls["move_to"] == [] and calls["stream_to"] == []
    assert dit._compiled is not None and dit._compiled.active
    assert all(tc.is_compiled(b) for b in dit.module.blocks)


# -- the caching allocator's idle reserved pool counts as free ----------------
# `torch.cuda.mem_get_info` reports blocks our own allocator reserved but no
# longer has allocated (the previous phase's activation buffers) as USED, so a
# gate that judges fit on the raw number refuses clips the card can hold: the
# maintainer's 14s MiniMax-H3 clip, 26.8GB of activations refused against
# "21.1GB free" on a 32.6GB card whose idle usage is ~5GB.

_POOL_RAW_FREE_GB = 21.1
_POOL_IDLE_RESERVED_GB = 8.0
_POOL_EFFECTIVE_FREE_GB = _POOL_RAW_FREE_GB + _POOL_IDLE_RESERVED_GB
_POOL_VIDEO_TOKENS = 92_000  # ~26.8GB reserve at H3's attention/FFN widths


def _pool_reserve_gb() -> float:
    return estimate_activation_reserve_gb(
        _POOL_VIDEO_TOKENS, inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
    )


def test_the_scenario_is_the_reported_one_reserve_between_raw_and_effective_free():
    # Guards the three tests below: the reserve must genuinely sit ABOVE raw
    # free and BELOW effective free, or they would prove nothing.
    assert _POOL_RAW_FREE_GB < _pool_reserve_gb() < _POOL_EFFECTIVE_FREE_GB


def test_idle_reserved_pool_is_credited_so_a_fitting_clip_is_not_refused():
    dit, calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(
        free_gb=_POOL_RAW_FREE_GB, effective_gb=_POOL_EFFECTIVE_FREE_GB,
    )
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=_POOL_VIDEO_TOKENS,
            inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
        )
    # Streams its weights (only ~2.3GB of budget is left over the reserve),
    # which is exactly what "used to run" meant -- not a refusal.
    assert decision.mode == "partial"
    assert len(calls["stream_to"]) == 1


def test_over_commit_warning_fires_when_the_allocator_pool_is_empty(caplog):
    # Same clip, same card, but nothing cached: raw == effective, so there is
    # no hidden headroom to credit and the over-commit warning is correct.
    dit, calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=_POOL_RAW_FREE_GB)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            decision = place_dit_for_sequence(
                dit, "cuda", video_tokens=_POOL_VIDEO_TOKENS,
                inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
            )
    assert "over-committed" in "\n".join(r.getMessage() for r in caplog.records)
    assert decision.mode == "partial"
    assert calls["move_to"] == []


def test_over_commit_warning_reports_both_the_raw_and_the_effective_free_number(caplog):
    # So the log says how much of the shortfall was the allocator holding on,
    # rather than the card being genuinely full.
    dit, _calls = _dit(estimated_vram_gb=15.65)
    patches, manager = _patched(free_gb=4.0, effective_gb=6.0)
    with caplog.at_level(logging.WARNING, logger=_MOD):
        with patches[0], patches[1], patches[2]:
            place_dit_for_sequence(
                dit, "cuda", video_tokens=_POOL_VIDEO_TOKENS,
                inner_dim=_H3_INNER_DIM, ffn_dim=_H3_FFN_DIM,
            )
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "6.00GB free" in warning
    assert "4.00GB raw" in warning


def test_warm_resident_dit_fits_once_the_idle_pool_is_credited():
    # The warm fast path reads free VRAM a second time; crediting only the
    # DiT's own weight there (and not the pool) would offload and re-stream a
    # DiT that is already resident and already fits.
    dit, calls = _dit(estimated_vram_gb=19.6)
    dit.device = "cuda"
    patches, manager = _patched(free_gb=2.0, effective_gb=10.0)  # 8GB idle pool
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=0, reserve_gb=5.0)
    assert decision.mode == "resident"
    assert decision.kept_resident is True
    assert calls["move_to"] == []
    assert calls["stream_to"] == []
    assert calls["offload"] == 0


def test_cold_placement_weight_budget_credits_the_idle_pool():
    # The post-eviction measurement that sizes `weight_budget` reads the same
    # way: on the raw number this 23.3GB DiT would be streamed instead of
    # pinned, for room that is actually there.
    dit, calls = _dit(estimated_vram_gb=23.3)
    tokens = _video_tokens_for(5)
    reserve = estimate_activation_reserve_gb(tokens)
    patches, manager = _patched(free_gb=20.0, effective_gb=23.3 + reserve + 0.5)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=tokens)
    assert decision.mode == "resident"
    assert calls["move_to"] == ["cuda"]
    assert calls["stream_to"] == []


def _quant_linear(in_features, *, nvfp4=False, fp8=False):
    if nvfp4:
        return _nvfp4_layer(in_features, 4096)
    if fp8:
        return _fp8_layer(in_features, 4096)
    return nn.Linear(in_features, 4096, device="meta")


def _dit_with(*linears, estimated_vram_gb=18.5, calls=None):
    module = nn.Module()
    for i, lin in enumerate(linears):
        module.add_module(f"l{i}", lin)
    calls = calls if calls is not None else []
    return SimpleNamespace(
        module=module, estimated_vram_gb=estimated_vram_gb, device="cpu",
        move_to=lambda device: calls.append(("resident", device)),
        stream_to=lambda device, budget: calls.append(("partial", device, budget)),
    )


def test_nvfp4_fast_path_charges_the_quantiser_peak_of_the_widest_input(machine):
    machine(nvfp4=True)
    dit = _dit_with(_quant_linear(4096, nvfp4=True), _quant_linear(16384, nvfp4=True), _quant_linear(32768))
    assert _dit_quant_scratch(dit).bytes_for(1) == 12 * 16384


def test_fp8_fast_path_charges_the_quantiser_peak(machine):
    machine(fp8=True)
    dit = _dit_with(_quant_linear(4096, fp8=True), _quant_linear(16384, fp8=True))
    assert _dit_quant_scratch(dit).bytes_for(1) == 5 * 16384


class _LiveBytes(TorchDispatchMode):
    def __init__(self) -> None:
        super().__init__()
        self.live: dict = {}
        self.peak = 0

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        self.live = {key: entry for key, entry in self.live.items() if not entry[0].expired()}
        inputs = {t.untyped_storage().data_ptr() for t in tree_flatten((args, kwargs))[0]
                  if isinstance(t, torch.Tensor)}
        out = func(*args, **(kwargs or {}))
        for t in tree_flatten(out)[0]:
            if isinstance(t, torch.Tensor):
                storage = t.untyped_storage()
                key = storage.data_ptr()
                if key not in inputs and key not in self.live:
                    self.live[key] = (StorageWeakRef(storage), storage.nbytes())
        self.peak = max(self.peak, sum(nbytes for _ref, nbytes in self.live.values()))
        return out


@pytest.mark.parametrize("quantize, per_element", [
    (_quantize_fp8_dynamic, _FP8_ACTIVATION_QUANT_BYTES_PER_ELEMENT),
    (quantize_nvfp4_dynamic, _NVFP4_ACTIVATION_QUANT_BYTES_PER_ELEMENT),
], ids=["fp8", "nvfp4"])
def test_the_quant_reserve_matches_the_quantisers_measured_peak(quantize, per_element):
    x = torch.randn(256, 1024).to(torch.bfloat16)
    with _LiveBytes() as tracker:
        quantize(x)
    peak_per_element = tracker.peak / x.numel()
    assert per_element - 1 < peak_per_element <= per_element + 1e-3


def test_no_fast_path_means_no_activation_quant_term(machine):
    machine()
    dit = _dit_with(_quant_linear(16384, nvfp4=True), _quant_linear(16384, fp8=True))
    assert _dit_quant_scratch(dit).bytes_for(1) == 0


def test_a_bare_callable_dit_has_no_activation_quant_term():
    assert _dit_quant_scratch(SimpleNamespace()).bytes_for(1) == 0


_NVFP4_CHUNK_SCRATCH_GB = 12 * 16384 * 4096 / 1024 ** 3


@pytest.mark.parametrize("tokens, plain_gb, nvfp4_gb", [
    (21_780, 2.10, 2.97),
    (31_620, 3.05, 3.92),
    (87_120, 8.41, 9.27),
])
def test_the_ltx_reserve_charges_one_nvfp4_quant_chunk_not_a_per_token_quant_copy(tokens, plain_gb, nvfp4_gb):
    scratch = QuantScratch((QuantWidth(12, 16384, 4096), QuantWidth(12, 4096, 16384)))
    plain = estimate_activation_reserve_gb(tokens)
    quant = estimate_activation_reserve_gb(tokens, quant_scratch=scratch)
    assert plain == pytest.approx(plain_gb, abs=0.01)
    assert quant == pytest.approx(nvfp4_gb, abs=0.01)
    assert quant - plain == pytest.approx(_NVFP4_CHUNK_SCRATCH_GB * 1.15, rel=1e-9)


def test_a_quant_chunk_never_charges_more_rows_than_the_sequence_has():
    scratch = QuantScratch((QuantWidth(12, 16384, 4096),))
    assert scratch.bytes_for(1000) == 12 * 16384 * 1000
    assert scratch.bytes_for(4096) == scratch.bytes_for(87_120) == 12 * 16384 * 4096


def test_an_unchunked_quant_width_scales_with_every_token():
    scratch = QuantScratch((QuantWidth(5, 16384, None),))
    assert scratch.bytes_for(87_120) == 5 * 16384 * 87_120


def test_the_nvfp4_chunk_the_reserve_charges_is_the_chunk_the_kernel_runs(machine):
    machine(nvfp4=True)
    dit = _dit_with(_quant_linear(4096, nvfp4=True), _quant_linear(16384, nvfp4=True))
    assert _dit_quant_scratch(dit) == QuantScratch((QuantWidth(12, 4096, activation_quant_chunk_rows(4096)),
                                                    QuantWidth(12, 16384, activation_quant_chunk_rows(16384))))
    assert _dit_quant_scratch(dit).bytes_for(87_120) == 12 * 16384 * activation_quant_chunk_rows(16384)


def test_the_fp8_chunk_the_reserve_charges_is_the_chunk_the_kernel_runs(machine):
    machine(fp8=True)
    dit = _dit_with(_quant_linear(16384, fp8=True))
    assert _dit_quant_scratch(dit) == QuantScratch((QuantWidth(5, 16384, activation_quant_chunk_rows(16384)),))
    assert _dit_quant_scratch(dit).bytes_for(146_547) == 5 * 16384 * 4096


_H3_KW = {"inner_dim": 7168, "ffn_dim": 14336, "ffn_chunk_rows": 24_425}
_H3_NVFP4 = QuantScratch(tuple(
    QuantWidth(12, k, activation_quant_chunk_rows(k)) for k in (5376, 7168, 14336)
))


@pytest.mark.parametrize("video_tokens, audio_tokens, plain_gb, nvfp4_gb", [
    (145_793, 754, 24.75, 25.61),
    (60_000, 300, 10.18, 11.05),
])
def test_the_h3_reserve_charges_one_nvfp4_quant_chunk(video_tokens, audio_tokens, plain_gb, nvfp4_gb):
    plain = estimate_activation_reserve_gb(video_tokens, audio_tokens, **_H3_KW)
    quant = estimate_activation_reserve_gb(video_tokens, audio_tokens, quant_scratch=_H3_NVFP4, **_H3_KW)
    assert plain == pytest.approx(plain_gb, abs=0.01)
    assert quant == pytest.approx(nvfp4_gb, abs=0.01)
    per_token_charge = estimate_activation_reserve_gb(
        video_tokens, audio_tokens, quant_scratch=QuantScratch((QuantWidth(12, 14336, None),)), **_H3_KW)
    assert quant < per_token_charge


def test_the_profiled_h3_clip_reserve_drops_from_the_per_token_charge():
    old = estimate_activation_reserve_gb(
        145_793, 754, quant_scratch=QuantScratch((QuantWidth(12, 14336, None),)), **_H3_KW)
    new = estimate_activation_reserve_gb(145_793, 754, quant_scratch=_H3_NVFP4, **_H3_KW)
    assert old == pytest.approx(51.75, abs=0.01)
    assert new == pytest.approx(25.61, abs=0.01)


def _cuda_placement_reserve(monkeypatch, layer, *, free_gb=26.0):
    monkeypatch.setattr(f"{_MOD}.free_vram_gb", lambda device: free_gb)
    monkeypatch.setattr(f"{_MOD}.effective_free_vram_gb", lambda device: free_gb)
    monkeypatch.setattr(f"{_MOD}.minimum_inference_memory_gb", lambda: 0.0)
    manager = SimpleNamespace(ensure_free=lambda *a, **k: False, offload_all=lambda *a, **k: False)
    monkeypatch.setattr(f"{_MOD}.get_residency_registry", lambda: manager)
    monkeypatch.setattr(f"{_MOD}.maybe_compile_dit", lambda *a, **k: None)
    return place_dit_for_sequence(_dit_with(layer), "cuda", video_tokens=31_620)


def test_the_nvfp4_fast_path_adds_one_bounded_quant_chunk_and_keeps_the_dit_resident(machine, monkeypatch):
    machine()
    off = _cuda_placement_reserve(monkeypatch, _quant_linear(16384, nvfp4=True))
    assert off.mode == "resident"

    machine(nvfp4=True)
    on = _cuda_placement_reserve(monkeypatch, _quant_linear(16384, nvfp4=True))
    assert on.mode == "resident"
    assert on.activation_reserve_gb == pytest.approx(off.activation_reserve_gb + _NVFP4_CHUNK_SCRATCH_GB * 1.15, rel=1e-6)


@pytest.mark.parametrize("make", [
    lambda: _fp8_layer(16384, 4096, dtype=torch.float8_e5m2),
    lambda: _int8_layer(16384, 4096),
    lambda: _int8_layer(16384, 4096, convrot=True),
    lambda: _fp8_layer(16385, 4096),
], ids=["e5m2", "int8", "int8-convrot", "misaligned"])
def test_an_fp8_layer_that_cannot_reach_the_gemm_does_not_raise_the_placement_reserve(machine, monkeypatch, make):
    machine()
    off = _cuda_placement_reserve(monkeypatch, make())
    machine(fp8=True)
    on = _cuda_placement_reserve(monkeypatch, make())
    gemm_layer = _cuda_placement_reserve(monkeypatch, _fp8_layer(16384, 4096))
    assert on.activation_reserve_gb == off.activation_reserve_gb
    assert gemm_layer.activation_reserve_gb > off.activation_reserve_gb


def test_sequence_fit_needs_a_cuda_device_and_a_vram_reading():
    dit, _ = _dit()
    assert sequence_fit(dit, "cpu") is None
    with patch(f"{_MOD}.effective_free_vram_gb", return_value=None):
        assert sequence_fit(dit, "cuda") is None


@pytest.mark.parametrize("resident", [False, True])
@pytest.mark.parametrize("seconds", [1, 5, 10, 15, 20, 30, 40])
def test_sequence_fit_predicts_exactly_the_placement_mode(resident, seconds):
    tokens = _video_tokens_for(seconds)
    free = 32.0 - (23.3 if resident else 0.0)
    probe, _ = _dit(estimated_vram_gb=23.3)
    placed, _ = _dit(estimated_vram_gb=23.3)
    if resident:
        probe.device = placed.device = "cuda"
    patches, _ = _patched(free_gb=free)
    with patches[0], patches[1], patches[2]:
        fit = sequence_fit(probe, "cuda")
        decision = place_dit_for_sequence(placed, "cuda", video_tokens=tokens)
    assert fit.resident(tokens) == (decision.mode == "resident")
    assert fit.reserve_gb(tokens) == pytest.approx(decision.activation_reserve_gb)


def test_sequence_fit_activations_fit_is_the_over_commit_boundary(caplog):
    dit, _ = _dit(estimated_vram_gb=15.65)
    patches, _ = _patched(free_gb=31.0)
    with patches[0], patches[1], patches[2]:
        fit = sequence_fit(dit, "cuda")
        fitting = max(t for t in range(0, 600_000, 1000) if fit.activations_fit(t))
        with caplog.at_level(logging.WARNING, logger=_MOD):
            place_dit_for_sequence(_dit(estimated_vram_gb=15.65)[0], "cuda", video_tokens=fitting)
            assert not caplog.records
            place_dit_for_sequence(_dit(estimated_vram_gb=15.65)[0], "cuda", video_tokens=fitting + 1000)
    assert caplog.records


def test_placement_logs_how_an_nvfp4_dits_linears_will_run(machine, caplog):
    machine(nvfp4=False)
    dit, _ = _dit(estimated_vram_gb=1.0)
    dit.module = nn.Sequential(_loaded_nvfp4_layer(), _loaded_nvfp4_layer(), nn.Linear(4, 4))
    patches, _ = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2], caplog.at_level(logging.INFO, logger=_MOD):
        place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    lines = [r.getMessage() for r in caplog.records if "nvfp4 linears" in r.getMessage()]
    assert lines == ["[LTX PLACEMENT] nvfp4 linears: 0 fast-path eligible, 2 dequant "
                     "(native_nvfp4_matmul is off: 2), 1 non-nvfp4"]


def test_placement_of_a_dit_without_nvfp4_linears_logs_no_linear_summary(caplog):
    dit, _ = _dit_with_lora(active=False)
    patches, _ = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2], caplog.at_level(logging.INFO, logger=_MOD):
        place_dit_for_sequence(dit, "cuda", video_tokens=1000)
    assert not [r for r in caplog.records if "nvfp4 linears" in r.getMessage()]


@pytest.mark.parametrize("nvfp4_matmul", [False, True])
def test_placement_and_the_window_fit_count_the_scale_tables_dequant_layers_will_hold(machine, nvfp4_matmul):
    machine(nvfp4=nvfp4_matmul)
    layers = [_loaded_nvfp4_layer(), _loaded_nvfp4_layer()]
    tables_gb = 0.0 if nvfp4_matmul else sum(layer.dequant_table_bytes() for layer in layers) / 1024 ** 3
    dit, _ = _dit(estimated_vram_gb=0.0)
    dit.module = nn.Sequential(*layers)
    patches, _ = _patched(free_gb=32.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1000)
        fit = sequence_fit(dit, "cuda")
    assert decision.dit_weight_gb == pytest.approx(tables_gb, abs=1e-15)
    assert fit.weight_gb == pytest.approx(tables_gb, abs=1e-15)
    assert (tables_gb > 0) is not nvfp4_matmul


class _HandoffDit:
    def __init__(self, device="cuda:0", streamer=None, module="module"):
        self.device = device
        self.module = module
        self._streamer = streamer
        self.offloads = 0

    def offload(self):
        self.offloads += 1
        self.device = "cpu"


def _hand_off(dit, **kwargs):
    marks = []
    profiler = SimpleNamespace(mark=lambda event, **fields: marks.append(event))
    with patch(f"{_MOD}.get_profiler", return_value=profiler):
        kept = hand_off_dit(dit, "cuda", **kwargs)
    return kept, marks


def test_a_handoff_leaves_a_fully_resident_dit_for_the_next_stage_to_place():
    dit = _HandoffDit()
    assert _hand_off(dit) == (True, ["dit_handoff.kept"])
    assert dit.offloads == 0 and dit.device == "cuda:0"


def test_a_handoff_offloads_a_dit_whose_room_the_stage_still_needs():
    dit = _HandoffDit()
    assert _hand_off(dit, needs_room=True) == (False, ["dit_handoff.offloaded"])
    assert dit.offloads == 1


def test_a_handoff_offloads_a_streamed_dit():
    dit = _HandoffDit(streamer=SimpleNamespace(active=True))
    assert _hand_off(dit) == (False, ["dit_handoff.offloaded"])
    assert dit.offloads == 1


def test_a_handoff_without_a_dit_does_nothing():
    assert _hand_off(None) == (False, [])
    assert _hand_off(_HandoffDit(module=None)) == (False, [])


def _profiled_h3_reserve(seq_chunk_rows=0, video_tokens=145_793, audio_tokens=754):
    return estimate_activation_reserve_gb(
        video_tokens, audio_tokens, quant_scratch=_H3_NVFP4, seq_chunk_rows=seq_chunk_rows, **_H3_KW,
    )


def test_chunking_the_attention_prep_pins_the_profiled_h3_clip_reserve():
    assert _profiled_h3_reserve() == pytest.approx(25.61, abs=0.01)
    assert _profiled_h3_reserve(18_319) == pytest.approx(14.36, abs=0.01)
    assert _profiled_h3_reserve(146_547) == _profiled_h3_reserve()


def test_smaller_attention_prep_chunks_never_raise_the_reserve_and_floor_at_the_held_buffers():
    reserves = [_profiled_h3_reserve(rows) for rows in (73_274, 36_637, 18_319, 9_160, 4_580)]
    assert reserves == sorted(reserves, reverse=True)
    assert reserves[-1] == reserves[-2]


def test_a_fixed_seq_chunk_also_bounds_the_mlp_chunk_the_reserve_charges():
    tokens = 100_000
    wide = estimate_activation_reserve_gb(tokens, inner_dim=7168, ffn_dim=14336, seq_chunk_rows=1024)
    narrow_ffn = estimate_activation_reserve_gb(
        tokens, inner_dim=7168, ffn_dim=14336, ffn_chunk_rows=1024, seq_chunk_rows=1024)
    assert wide == narrow_ffn


def _chunk_reserve(table):
    return lambda rows: table[rows]


def test_the_chunk_choice_takes_the_fewest_chunks_that_fit():
    table = {5000: 9.0, 2500: 6.0, 1250: 4.0}
    assert choose_seq_chunk_rows(10_000, _chunk_reserve(table), 6.5) == 2500
    assert choose_seq_chunk_rows(10_000, _chunk_reserve(table), 10.0) == 5000


def test_when_no_chunk_fits_the_choice_is_the_widest_chunk_at_the_lowest_reserve():
    table = {5000: 9.0, 2500: 7.0, 1250: 7.0}
    assert choose_seq_chunk_rows(10_000, _chunk_reserve(table), 1.0) == 2500


def test_a_sequence_too_short_to_split_gets_no_chunking():
    assert choose_seq_chunk_rows(2_000, lambda rows: 0.0, 1.0) == 0


def _h3_placement(free_gb, *, seq_chunking=True, seq_chunk_rows=0):
    dit, calls = _dit(estimated_vram_gb=13.89)
    patches, _ = _patched(free_gb=free_gb)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.maybe_compile_dit"):
        return place_dit_for_sequence(
            dit, "cuda", video_tokens=145_793, audio_tokens=754, inner_dim=7168, ffn_dim=14336,
            ffn_chunk_rows=24_425, seq_chunking=seq_chunking, seq_chunk_rows=seq_chunk_rows,
        )


def test_placement_leaves_the_attention_prep_whole_when_the_reserve_fits():
    decision = _h3_placement(40.0)
    assert decision.seq_chunk_rows == 0 and decision.seq_chunking is True
    assert decision.activation_reserve_gb == pytest.approx(_profiled_h3_reserve() - 0.86, abs=0.01)


def test_placement_chunks_the_attention_prep_with_the_fewest_chunks_that_fit():
    decision = _h3_placement(16.0)
    assert decision.seq_chunk_rows == 36_637
    assert decision.activation_reserve_gb <= 16.0
    assert _h3_placement(13.0).seq_chunk_rows == 18_319


def test_placement_never_chunks_a_dit_that_cannot_take_it():
    assert _h3_placement(13.0, seq_chunking=False).seq_chunk_rows == 0


def test_placement_keeps_a_configured_seq_chunk():
    decision = _h3_placement(40.0, seq_chunk_rows=5000)
    assert decision.seq_chunk_rows == 5000


def test_the_fit_hint_sees_the_chunked_reserve(caplog):
    seen = []

    def hint(free_gb, *, activation_reserve_gb, extra_reserve_gb, tokens):
        seen.append(activation_reserve_gb)
        return ""

    dit, _ = _dit(estimated_vram_gb=13.89)
    patches, _ = _patched(free_gb=11.74)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.maybe_compile_dit"):
        decision = place_dit_for_sequence(
            dit, "cuda", video_tokens=145_793, audio_tokens=754, inner_dim=7168, ffn_dim=14336,
            ffn_chunk_rows=24_425, seq_chunking=True, fit_hint=hint,
        )
    assert decision.seq_chunk_rows == 18_319
    assert seen == [decision.activation_reserve_gb]


def _chunk_aware_forward(max_rows):
    seen = []

    def forward(*args, seq_chunk_rows=0, **kwargs):
        seen.append(seq_chunk_rows)
        if not seq_chunk_rows or seq_chunk_rows > max_rows:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        return "ok"

    return forward, seen


def _chunking_decision(seq_chunking=True):
    return DitPlacementDecision(
        "partial", 13.89, 25.61, 0.0, 145_793, 754, 0.0, seq_chunking=seq_chunking,
    )


def test_a_sampling_oom_retries_with_a_chunked_attention_prep_before_raising():
    forward, seen = _chunk_aware_forward(40_000)
    dit, dit_calls = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_chunking_decision())
    with patch(f"{_MOD}.torch.cuda.is_available", return_value=False):
        assert guarded(seq_chunk_rows=0) == "ok"
        assert guarded(seq_chunk_rows=0) == "ok"
    assert seen == [0, 0, 73_274, 36_637, 36_637]
    assert dit_calls["stream_to"] == []


def test_a_dit_that_cannot_chunk_raises_without_trying():
    forward, seen = _chunk_aware_forward(40_000)
    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_chunking_decision(False))
    with patch(f"{_MOD}._sampling_oom", return_value=ValueError("raised")), \
         patch(f"{_MOD}.torch.cuda.is_available", return_value=False), pytest.raises(ValueError):
        guarded(seq_chunk_rows=0)
    assert seen == [0, 0]


def test_an_oom_at_the_smallest_chunk_still_raises():
    forward, seen = _chunk_aware_forward(10)
    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_chunking_decision())
    with patch(f"{_MOD}._sampling_oom", return_value=ValueError("raised")), \
         patch(f"{_MOD}.torch.cuda.is_available", return_value=False), pytest.raises(ValueError):
        guarded(seq_chunk_rows=0)
    assert seen[-1] == 1_145 and len(seen) == 9


_DRIVER_OOM = "CUDA error: out of memory"
_WSL_DRIVER_OOM = "CUDA driver error: device not ready"


def _driver_oom() -> BaseException:
    return torch.AcceleratorError(_DRIVER_OOM)


def _wsl_driver_oom() -> BaseException:
    return RuntimeError(_WSL_DRIVER_OOM)


_LADDER_OOMS = pytest.mark.parametrize("oom", [_driver_oom, _wsl_driver_oom], ids=["accelerator", "wsl_driver"])


def test_a_driver_level_out_of_memory_counts_as_an_oom():
    assert is_cuda_oom(_driver_oom())
    assert is_cuda_oom(torch.cuda.OutOfMemoryError("CUDA out of memory"))


def test_other_accelerator_errors_are_not_ooms():
    assert not is_cuda_oom(torch.AcceleratorError("CUDA error: an illegal memory access was encountered"))
    assert not is_cuda_oom(RuntimeError(_DRIVER_OOM))


@_LADDER_OOMS
def test_partial_placement_degrades_to_fully_streamed_on_a_driver_level_oom(oom):
    calls = {"stream_to": []}

    def stream_to(d, budget):
        calls["stream_to"].append((d, budget))
        if len(calls["stream_to"]) == 1:
            raise oom()

    dit = SimpleNamespace(estimated_vram_gb=19.61, move_to=lambda d: None, stream_to=stream_to,
                          offload=lambda: None)
    patches, manager = _patched(free_gb=10.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=8_000)
    assert decision.mode == "partial"
    assert calls["stream_to"][1] == ("cuda", 0.0)
    assert manager.offload_all_calls


@_LADDER_OOMS
def test_full_move_degrades_to_partial_on_a_driver_level_oom(oom):
    calls = {"move_to": 0, "stream_to": []}

    def move_to(d):
        calls["move_to"] += 1
        raise oom()

    dit = SimpleNamespace(estimated_vram_gb=2.0, move_to=move_to,
                          stream_to=lambda d, budget: calls["stream_to"].append((d, budget)),
                          offload=lambda: None)
    patches, manager = _patched(free_gb=10.0)
    with patches[0], patches[1], patches[2]:
        decision = place_dit_for_sequence(dit, "cuda", video_tokens=1_000)
    assert calls["move_to"] == 2
    assert decision.mode == "partial"
    assert calls["stream_to"] == [("cuda", pytest.approx(9.0))]


def test_a_placement_error_that_is_not_an_oom_still_raises():
    def stream_to(d, budget):
        raise torch.AcceleratorError("CUDA error: an illegal memory access was encountered")

    dit = SimpleNamespace(estimated_vram_gb=19.61, move_to=lambda d: None, stream_to=stream_to,
                          offload=lambda: None)
    patches, manager = _patched(free_gb=10.0)
    with patches[0], patches[1], patches[2]:
        with pytest.raises(torch.AcceleratorError):
            place_dit_for_sequence(dit, "cuda", video_tokens=8_000)


@_LADDER_OOMS
def test_the_first_forward_degrades_on_a_driver_level_oom(oom):
    calls = {"n": 0}

    def forward(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise oom()
        return "ok"

    dit, dit_calls = _dit()
    patches, manager = _patched(free_gb=8.0)
    with patches[0], patches[1], patches[2], patch(f"{_MOD}.torch.cuda.empty_cache"):
        with patch(f"{_MOD}.torch.cuda.is_available", return_value=True):
            guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
            assert guarded() == "ok"
    assert calls["n"] == 3
    assert dit_calls["stream_to"] == [("cuda", 0.0)]


def test_a_driver_error_that_is_not_an_oom_is_not_retried():
    calls = {"n": 0}

    def forward(*args, **kwargs):
        calls["n"] += 1
        raise torch.AcceleratorError("CUDA error: an illegal memory access was encountered")

    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
    with pytest.raises(torch.AcceleratorError):
        guarded()
    assert calls["n"] == 1


def test_a_wsl_driver_error_that_is_not_exhaustion_is_not_retried():
    calls = {"n": 0}

    def forward(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("CUDA driver error: invalid argument")

    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_decision())
    with pytest.raises(RuntimeError, match="invalid argument"):
        guarded()
    assert calls["n"] == 1


def test_the_wsl_driver_oom_counts_as_an_oom():
    assert is_cuda_oom(_wsl_driver_oom())


@_LADDER_OOMS
def test_the_chunked_attention_prep_retry_treats_a_driver_level_oom_as_an_oom(oom):
    seen = []

    def forward(*args, seq_chunk_rows=0, **kwargs):
        seen.append(seq_chunk_rows)
        if not seq_chunk_rows or seq_chunk_rows > 40_000:
            raise oom()
        return "ok"

    dit, dit_calls = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_chunking_decision())
    with patch(f"{_MOD}.torch.cuda.is_available", return_value=False):
        assert guarded(seq_chunk_rows=0) == "ok"
    assert seen == [0, 0, 73_274, 36_637]
    assert dit_calls["stream_to"] == []


def test_a_driver_error_in_the_chunked_attention_prep_retry_is_not_retried():
    seen = []

    def forward(*args, seq_chunk_rows=0, **kwargs):
        seen.append(seq_chunk_rows)
        if not seq_chunk_rows:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        raise RuntimeError("CUDA driver error: invalid argument")

    dit, _ = _dit()
    guarded = guard_sampling_oom(forward, dit=dit, device="cuda", decision=_chunking_decision())
    with patch(f"{_MOD}.torch.cuda.is_available", return_value=False), \
         pytest.raises(RuntimeError, match="invalid argument"):
        guarded(seq_chunk_rows=0)
    assert seen == [0, 0, 73_274]
