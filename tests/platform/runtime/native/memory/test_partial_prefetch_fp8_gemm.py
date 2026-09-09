"""Integration tests for Fp8ScaledLinear's opt-in fp8 GEMM fast path (INF-06),
dispatched through the REAL ``ModuleStreamer.apply`` / ``LayerPrefetcher``
apply -> consume -> restore lifecycle (``memory/partial.py``) -- not a
hand-built streamed leaf.

Why this file exists: the original INF-06 dispatch tests in
``tests/platform/runtime/native/test_weight_ops.py`` construct a streamed
leaf's residency by hand and never cross the real streamer/prefetcher
machinery, so they could not prove the operand a genuine partial-residency
run stages is the one that actually reaches the kernel. Their fake kernel
also returned ``torch.zeros(...)`` regardless of its inputs, so an
``assert_equal(out, fake_out)`` + call-count check could not have caught a
wrong or zeroed staged operand. This file fixes both gaps: real streamer
lifecycle, and a fake kernel that COMPUTES its result from the actual
operands it receives.

CPU-only box: CUDA stream/event/copy primitives for ``LayerPrefetcher`` are
faked by reusing ``_install_fake_cuda``/``_streamed_leaves`` from
``test_partial_prefetch.py`` (not reinvented); "already on the activation
device" for ops.py's own on-demand-staging check is faked via an
``is_cuda`` id-EXCLUSION set (default resident=True, so an activation or any
intermediate the real model computes never needs individually marking --
only the leaf weight Parameters that must start off-device are named), the
same idiom used in ``tests/platform/runtime/native/arch/
test_minimax_h3_prepared_linear.py``.
"""

from __future__ import annotations

import gc
import weakref

import pytest
import torch
import torch.nn as nn

import vendor.gpl.comfyui.ops as wo
from src.platform.runtime.native.lora.key_mapping import LoraDelta
from src.platform.runtime.native.memory.partial import ModuleStreamer, plan_residency_split
from vendor.gpl.comfyui.ops import NATIVE_FP8_MATMUL_ENV, fp8_ops

from .test_partial_prefetch import _install_fake_cuda

# --- fixtures ------------------------------------------------------------


class _Fp8Chain(nn.Module):
    """Two sequential fp8-scaled Linears, nothing else. No Embedding (never
    streamable) and no other own tensors on the root -- every leaf here is
    streamable, so ``plan_residency_split(resident_budget_gb=0.0)`` streams
    the whole model and ``ModuleStreamer.apply``'s "resident" branch never
    touches a real tensor (the root container itself owns none), which is
    what lets ``apply("cuda:0", ...)`` run for real on this CPU-only box
    without needing to stub ``_move_own_tensors``."""

    def __init__(self, dim: int = 16, bias: bool = False) -> None:
        super().__init__()
        self.a = fp8_ops.Linear(dim, dim, bias=bias)
        self.b = fp8_ops.Linear(dim, dim, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.b(self.a(x))


class _Fp8Leaf(nn.Module):
    """One fp8-scaled Linear, for the per-scenario numeric-parity tests
    (no successor to prefetch, prefetch is irrelevant to those)."""

    def __init__(self, dim: int = 16, bias: bool = False) -> None:
        super().__init__()
        self.lin = fp8_ops.Linear(dim, dim, bias=bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.lin(x)


def _load_fp8(
    lin: nn.Module, dim: int, seed: int, *,
    bias: torch.Tensor | None = None,
    input_scale: torch.Tensor | None = None,
    pre_quant_scale: torch.Tensor | None = None,
) -> None:
    """A real, small, nonzero fp8-scaled weight -- same construction
    test_weight_ops.py uses for a plain (non-nvfp4) Fp8ScaledLinear."""
    g = torch.Generator().manual_seed(seed)
    real_w = torch.randn(dim, dim, generator=g) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    sd = {"weight": w_fp8, "weight_scale": w_scale}
    if bias is not None:
        sd["bias"] = bias
    if input_scale is not None:
        sd["input_scale"] = input_scale
    if pre_quant_scale is not None:
        sd["pre_quant_scale"] = pre_quant_scale
    lin.load_state_dict(sd, strict=False, assign=True)


def _fp8_chain(dim: int = 16) -> _Fp8Chain:
    m = _Fp8Chain(dim)
    _load_fp8(m.a, dim, seed=11)
    _load_fp8(m.b, dim, seed=12)
    return m


def _streamed(m: nn.Module, *, prefetch: bool) -> ModuleStreamer:
    """Stream every leaf of ``m`` through the REAL ModuleStreamer.apply,
    targeting a (faked) CUDA device so LayerPrefetcher construction is
    exercised for real when ``prefetch=True``."""
    plan = plan_residency_split(m, resident_budget_gb=0.0)
    streamer = ModuleStreamer(m, prefetch=prefetch)
    streamer.apply("cuda:0", plan, pin=False, non_blocking=True)
    return streamer


def _recording_scaled_mm():
    """A fake ``torch._scaled_mm`` that COMPUTES its result from the actual
    operands it receives -- (a.float()*scale_a) @ (b.float()*scale_b) + bias
    -- instead of returning a fixed ``torch.zeros(...)``. A wrong or zeroed
    staged operand changes THIS result, unlike the old fake."""
    calls: list[dict] = []

    def fake(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        calls.append({"a_shape": tuple(a.shape), "b_shape": tuple(b.shape)})
        out = (a.to(torch.float32) * scale_a.to(torch.float32)) @ (b.to(torch.float32) * scale_b.to(torch.float32))
        if bias is not None:
            out = out + bias.to(torch.float32)
        return out.to(out_dtype)

    return calls, fake


def _install_fp8_residency(monkeypatch, *, excluded: set[int]):
    """Fake CUDA residency scoped to `excluded` (data-pointer values of the
    leaf weights that must start off-device): ``.is_cuda`` defaults True for
    every OTHER tensor (activations, intermediates, a prefetch-swapped clone,
    an on-demand-staged clone) via a ``data_ptr()`` EXCLUSION set, so nothing
    but the named weights ever needs individual marking.

    Keyed on ``data_ptr()``, not ``id()``: ``LayerPrefetcher._consume`` swaps
    residency via ``leaf.weight.data = gpu_w`` -- the ``nn.Parameter``
    object's OWN identity (``id(leaf.weight)``) never changes, only what
    storage it currently wraps, so an ``id()``-keyed fake could never observe
    that swap. ``data_ptr()`` does change (the swapped-in clone is a genuinely
    different allocation), matching how a real ``.is_cuda``/``.device``
    property actually behaves across a ``.data =`` reassignment.

    ``wo.cast_to`` always clones on a cast (the real function short-circuits
    to the SAME object when source/target device+dtype already coincide,
    which they always do on this CPU-only box, so a real clone is needed to
    make "staged vs. still-on-the-leaf" observable at all). ``excluded`` is a
    live set of data pointers the caller can mutate (e.g. ``.discard(...)``)
    to flip a weight from "streamed" to "resident" between two calls in the
    same test, as long as no swap has happened in between (a pointer, once
    swapped away from, no longer identifies that leaf's current storage)."""
    def _fake_cast_to(tensor, dtype, device, *, non_blocking=False):
        if tensor is None:
            return None
        return tensor.detach().to(dtype=dtype).clone()

    monkeypatch.setattr(torch.Tensor, "is_cuda", property(lambda self: self.data_ptr() not in excluded))
    monkeypatch.setattr(wo, "cast_to", _fake_cast_to)


def _dense_reference(m: nn.Module, x: torch.Tensor, monkeypatch) -> torch.Tensor:
    """The existing (already-validated) dequant path's output for the SAME
    model/input, with the fp8 GEMM gate off -- the oracle every fast-path
    output below is compared against."""
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    with torch.no_grad():
        return m(x)


# --- (1) real streamer/prefetcher lifecycle: prefetch off ------------------


def test_prefetch_off_on_demand_stage_reaches_kernel_with_real_dequant_parity(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    streamer = _streamed(m, prefetch=False)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    calls, fake_kernel = _recording_scaled_mm()

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        staged_out = m(x)

    assert len(calls) == 2  # one real GEMM per leaf, both via on-demand staging
    dense_out = _dense_reference(_fp8_chain(), x, monkeypatch)  # fresh, identical-seed model
    torch.testing.assert_close(staged_out.float(), dense_out.float(), rtol=0.15, atol=0.1)
    streamer.teardown()


# --- (1) real lifecycle: missed/first (recording) prefetch pass -----------


def test_recording_pass_with_prefetch_enabled_still_stages_on_demand(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    _install_fake_cuda(monkeypatch)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    streamer = _streamed(m, prefetch=True)
    assert streamer.prefetcher is not None  # real LayerPrefetcher, constructed by real apply()

    x = torch.randn(2, 16, dtype=torch.bfloat16)
    calls, fake_kernel = _recording_scaled_mm()

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        out = m(x)  # forward #1: the prefetcher's OWN recording pass -- stages nothing

    assert len(calls) == 2  # both leaves still reached the kernel, via on-demand staging
    dense_out = _dense_reference(_fp8_chain(), x, monkeypatch)
    torch.testing.assert_close(out.float(), dense_out.float(), rtol=0.15, atol=0.1)
    streamer.teardown()


# --- (1) real lifecycle: a genuine prefetch hit -----------------------------


def test_prefetch_hit_reaches_kernel_with_zero_extra_on_demand_staging(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    _install_fake_cuda(monkeypatch)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    streamer = _streamed(m, prefetch=True)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    _, fake_kernel = _recording_scaled_mm()

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        m(x)  # forward #1: records execution order a -> b, no prefetch yet

    # b's own on-demand staging must not fire on the prefetch-hit forward:
    # a's pre-hook prefetches b (its recorded successor) via the REAL
    # LayerPrefetcher._consume/_prefetch_after choreography, swapping
    # b.weight.data to a resident clone before b's forward ever runs.
    b_on_demand_calls: list = []
    real_stage = wo.Fp8ScaledLinear._stage_scaled_mm_weight

    def _spy_stage(self, device):
        result = real_stage(self, device)
        if self is m.b and result is not None and result is not self.weight:
            b_on_demand_calls.append(1)
        return result

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_stage_scaled_mm_weight", _spy_stage)
    calls2, fake_kernel2 = _recording_scaled_mm()

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel2)
    with torch.no_grad():
        out2 = m(x)  # forward #2: prefetch kicks in for b

    assert len(calls2) == 2               # both leaves still reached the kernel
    assert b_on_demand_calls == []        # ...but b staged nothing of its own -- reused the prefetch
    dense_out = _dense_reference(_fp8_chain(), x, monkeypatch)
    torch.testing.assert_close(out2.float(), dense_out.float(), rtol=0.15, atol=0.1)
    streamer.teardown()


# --- (1) real lifecycle: restore/offload leaves storage ownership intact ---


def test_teardown_restores_leaf_storage_ownership_after_fp8_dispatch(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    _install_fake_cuda(monkeypatch)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    streamer = _streamed(m, prefetch=True)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    _, fake_kernel = _recording_scaled_mm()
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        m(x)
        m(x)  # a real prefetch hit happens on this second call

    streamer.teardown()

    # Storage ownership: back to CPU, unpinned, no leftover swap from a
    # prefetch consume/restore cycle, flags reverted to namespace defaults.
    for leaf in (m.a, m.b):
        assert leaf.weight.data.device.type == "cpu"
        assert not leaf.weight.data.is_pinned()
        assert leaf.comfy_cast_weights is type(leaf).comfy_cast_weights
        assert "stream_non_blocking" not in leaf.__dict__
    assert streamer.active is False
    assert streamer.prefetcher is None

    # And the model still works correctly afterward (gate off -- plain dequant).
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    with torch.no_grad():
        out = m(x)
    assert out.shape == (2, 16)


# --- (3) the eligible staged path performs no dense weight dequant ---------


def test_eligible_staged_path_never_dequantizes_the_weight(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    dequant_calls: list = []
    real_prepare = wo.Fp8ScaledLinear._prepare_dequant_operand

    def _spy_prepare(self, dtype, device, **kwargs):
        dequant_calls.append(1)
        return real_prepare(self, dtype, device, **kwargs)

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_prepare_dequant_operand", _spy_prepare)

    streamer = _streamed(m, prefetch=False)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    _, fake_kernel = _recording_scaled_mm()
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        m(x)

    assert dequant_calls == []  # every leaf reached the kernel; dequant never ran
    streamer.teardown()


# --- (4) staged-operand lifetime: released after each forward -------------


def test_staged_operand_weakref_dies_after_each_forward(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    refs: list[weakref.ReferenceType] = []
    real_stage = wo.Fp8ScaledLinear._stage_scaled_mm_weight

    def _spy_stage(self, device):
        result = real_stage(self, device)
        if result is not None and result is not self.weight:
            refs.append(weakref.ref(result))  # id/weakref only -- never the tensor itself
        return result

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_stage_scaled_mm_weight", _spy_stage)

    streamer = _streamed(m, prefetch=False)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    _, fake_kernel = _recording_scaled_mm()
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel)
    with torch.no_grad():
        m(x)

    gc.collect()
    assert len(refs) == 2                       # one genuine stage per leaf
    assert all(r() is None for r in refs)        # none survive the forward that used them
    streamer.teardown()


# --- (4) staged-operand lifetime: released BEFORE the dense fallback runs -


def test_staged_operand_released_before_dense_fallback_on_kernel_rejection(monkeypatch):
    m = _fp8_chain()
    excluded = {m.a.weight.data_ptr(), m.b.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    staged_ref_by_leaf: dict[int, weakref.ReferenceType] = {}
    real_stage = wo.Fp8ScaledLinear._stage_scaled_mm_weight

    def _spy_stage(self, device):
        result = real_stage(self, device)
        if result is not None and result is not self.weight:
            staged_ref_by_leaf[id(self)] = weakref.ref(result)
        return result

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_stage_scaled_mm_weight", _spy_stage)

    dead_before_dequant: dict[int, bool] = {}
    real_prepare = wo.Fp8ScaledLinear._prepare_dequant_operand

    def _spy_prepare(self, dtype, device, **kwargs):
        gc.collect()
        ref = staged_ref_by_leaf.get(id(self))
        if ref is not None:
            dead_before_dequant[id(self)] = ref() is None
        return real_prepare(self, dtype, device, **kwargs)

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_prepare_dequant_operand", _spy_prepare)

    # Every kernel call is rejected (a real capability-probe-style rejection),
    # for BOTH leaves -- each independently falls back to dense dequant.
    def _rejecting_kernel(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        raise RuntimeError("simulated kernel-level rejection")

    streamer = _streamed(m, prefetch=False)
    x = torch.randn(2, 16, dtype=torch.bfloat16)
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _rejecting_kernel)
    with torch.no_grad():
        out = m(x)

    # Both leaves' rejected fast attempt fell back to dense dequant, and in
    # BOTH cases the staged operand was already dead by the time the dense
    # path ran -- never held across the fallback boundary.
    assert dead_before_dequant == {id(m.a): True, id(m.b): True}
    assert out.shape == (2, 16)
    streamer.teardown()


# --- (2) numeric correctness across scenarios: static/dynamic scale, AWQ, LoRA


def _make_scenario_leaf(
    scenario: str, dim: int = 16,
) -> tuple[_Fp8Leaf, list[LoraDelta] | None]:
    """Every random tensor here is drawn from a FIXED-seed generator (never
    the global RNG), so calling this twice for the same `scenario` -- once
    for the leaf under test, once for the fresh dense-dequant oracle -- is
    fully reproducible. An earlier version of this fixture used the global
    RNG for the bias/AWQ-scale/LoRA tensors, which silently made the two
    calls diverge and produced a spurious large numeric mismatch that looked
    like a fast-path bug but was purely a test-fixture defect."""
    _seeds = {
        "dynamic_input_scale": 101, "static_input_scale": 102,
        "awq_pre_quant_scale": 103, "with_bias": 104, "branchable_lora": 105,
    }
    g = torch.Generator().manual_seed(_seeds[scenario])
    bias = torch.randn(dim, generator=g) * 0.01 if scenario == "with_bias" else None
    input_scale = torch.tensor(0.02) if scenario == "static_input_scale" else None
    pre_quant_scale = torch.rand(dim, generator=g) * 0.5 + 0.75 if scenario == "awq_pre_quant_scale" else None
    m = _Fp8Leaf(dim, bias=bias is not None)
    _load_fp8(m.lin, dim, seed=21, bias=bias, input_scale=input_scale, pre_quant_scale=pre_quant_scale)
    lora_deltas = None
    if scenario == "branchable_lora":
        lora_deltas = [LoraDelta(
            down=torch.randn(4, dim, generator=g), up=torch.randn(dim, 4, generator=g),
            alpha=4.0, scale=1.0,
        )]
        m.lin.lora_deltas = lora_deltas
    return m, lora_deltas


@pytest.mark.parametrize(
    "scenario",
    ["dynamic_input_scale", "static_input_scale", "awq_pre_quant_scale", "with_bias", "branchable_lora"],
)
def test_staged_path_matches_resident_path_and_dense_reference(monkeypatch, scenario):
    m, _ = _make_scenario_leaf(scenario)
    excluded = {m.lin.weight.data_ptr()}
    _install_fp8_residency(monkeypatch, excluded=excluded)
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    streamer = _streamed(m, prefetch=False)
    x = torch.randn(3, 16, dtype=torch.bfloat16)

    _, fake_kernel_staged = _recording_scaled_mm()
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel_staged)
    with torch.no_grad():
        staged_out = m(x)  # weight excluded -> on-demand staged

    excluded.discard(m.lin.weight.data_ptr())  # now "resident" -- no staging needed
    _, fake_kernel_resident = _recording_scaled_mm()
    monkeypatch.setattr(torch, "_scaled_mm", fake_kernel_resident)
    with torch.no_grad():
        resident_out = m(x)

    assert torch.equal(staged_out, resident_out)  # same values regardless of residency

    # Reconstruct an identical (fresh) leaf for the dense-dequant oracle --
    # reusing `m` would carry over the streamed/gate-on state.
    ref_m, _ = _make_scenario_leaf(scenario)
    dense_out = _dense_reference(ref_m, x, monkeypatch)
    torch.testing.assert_close(staged_out.float(), dense_out.float(), rtol=0.2, atol=0.15)
    streamer.teardown()
