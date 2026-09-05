"""Tests for MiniMaxH3Attention's chunked qkv/out_proj projection loops reusing
ONE prepared weight/bias operand per projection call (see
``CastWeightBiasOp.prepared_linear`` / ``Fp8ScaledLinear.prepared_linear`` in
``vendor/gpl/comfyui/ops.py``), instead of re-dequantising (or, with the
opt-in fp8 GEMM gate on, re-staging) the same unchanged weight on every chunk
of ``seq_chunk_rows`` low-VRAM sequence chunking.

The interleaved MLP fc1/fc2 loop (``MiniMaxH3MLP.forward``) is explicitly OUT
of scope for this reuse (retaining both fc1 and fc2 operands at once would
raise the streamed working set) and is neither touched nor tested here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import torch
import torch.nn.functional as F

import src.platform.runtime.native.arch.minimax_h3.model as model_module
import vendor.gpl.comfyui.ops as wo
from src.platform.runtime.native.arch.minimax_h3.model import MiniMaxH3Attention
from vendor.gpl.comfyui.ops import NATIVE_FP8_MATMUL_ENV, disable_weight_init, fp8_ops


def _stub_attention_core(monkeypatch) -> None:
    """The attention core itself is out of scope here (never chunked, see
    MiniMaxH3Attention.forward) -- stubbed to plain SDPA so a bf16 activation
    (needed for the fp8 GEMM fast path's dtype precondition) doesn't reach the
    real backend auto-selector, which on this box picks a sage2 kernel whose
    native module fails to import in this container (see docs/testing-notes.md);
    mirrors test_minimax_h3_model.py's own OOM tests, which stub the same seam
    for the same reason."""
    monkeypatch.setattr(
        model_module, "_dispatch_attention",
        lambda q, k, v, heads=None, mask=None: F.scaled_dot_product_attention(q, k, v),
    )


def _init_norms(attn: MiniMaxH3Attention) -> None:
    """q_norm/k_norm weights start as raw allocator memory (disable-weight-init) --
    NaN would make every comparison below flake NaN-vs-NaN independent of any
    real bug, exactly the trap test_minimax_h3_model.py's own
    ``_init_attn_weights`` documents."""
    with torch.no_grad():
        attn.q_norm.weight.copy_(torch.ones_like(attn.q_norm.weight))
        attn.k_norm.weight.copy_(torch.ones_like(attn.k_norm.weight))


def _load_fp8(lin: torch.nn.Module, out_f: int, in_f: int, seed: int) -> None:
    """A real, small, nonzero fp8-scaled weight -- same construction
    test_weight_ops.py uses for a plain (non-nvfp4) Fp8ScaledLinear: no
    ``weight_scale_2`` key, so ``Nvfp4Linear`` (``fp8_ops.Linear``) takes its
    inherited ``Fp8ScaledLinear`` plain-fp8 branch, never the nvfp4 one."""
    g = torch.Generator().manual_seed(seed)
    real_w = torch.randn(out_f, in_f, generator=g) * 0.05
    w_scale = torch.tensor(0.01)
    w_fp8 = (real_w / w_scale).clamp(-448, 448).to(torch.float8_e4m3fn)
    lin.load_state_dict({"weight": w_fp8, "weight_scale": w_scale}, strict=False, assign=True)


def _fp8_attn(hidden: int = 16, heads: int = 2, head_dim: int = 8) -> MiniMaxH3Attention:
    """A tiny MiniMaxH3Attention whose qkv_proj/out_proj are real fp8-scaled
    layers. Fixed per-tensor seeds (not global ``torch.manual_seed``) so two
    separately constructed instances get IDENTICAL weights -- needed to
    compare an unchunked reference against a chunked run from a fresh
    instance without entangling global RNG state."""
    attn = MiniMaxH3Attention(hidden, heads, head_dim, 1e-5, fp8_ops, dtype=torch.float32)
    _init_norms(attn)
    inner_dim = heads * head_dim
    _load_fp8(attn.qkv_proj, 3 * inner_dim, hidden, seed=1)
    _load_fp8(attn.out_proj, hidden, inner_dim, seed=2)
    return attn


def _float_attn(hidden: int = 16, heads: int = 2, head_dim: int = 8) -> MiniMaxH3Attention:
    """Ordinary (non-quantised) control: plain disable_weight_init layers."""
    attn = MiniMaxH3Attention(hidden, heads, head_dim, 1e-5, disable_weight_init, dtype=torch.float32)
    with torch.no_grad():
        attn.qkv_proj.weight.copy_(torch.randn_like(attn.qkv_proj.weight) * 0.2)
        attn.out_proj.weight.copy_(torch.randn_like(attn.out_proj.weight) * 0.2)
    _init_norms(attn)
    return attn


def _install_fake_cuda_residency(monkeypatch, *, streamed: list[torch.Tensor]):
    """Fake a real GPU generation where every tensor is CUDA-resident EXCEPT
    the given ``streamed`` leaf weights, which start on (pinned) CPU RAM --
    exactly the ``ModuleStreamer.apply`` state a streamed fp8 leaf sits in.
    ``.is_cuda`` is an id-based EXCLUSION set (default resident=True), so the
    activation, every intermediate the attention core derives from it
    (RMSNorm/RoPE/attention output — none of them explicitly marked), and a
    staged clone of a streamed weight are all correctly "resident" without
    needing individual marking; only the two leaf weight Parameters
    themselves start excluded, and stay excluded (their own storage is never
    reassigned by staging). ``cast_to`` always clones on a streamed-weight
    cast, mirroring a real device move -- the real ``cast_to`` short-circuits
    to the SAME object when source/target device+dtype already coincide,
    which they always do on this CPU-only box."""
    streamed_ids = {id(t) for t in streamed}
    calls: list[tuple[int, torch.dtype, torch.device, bool]] = []

    def _fake_cast_to(tensor, dtype, device, *, non_blocking=False):
        if tensor is None:
            return None
        calls.append((id(tensor), dtype, device, non_blocking))
        return tensor.detach().to(dtype=dtype).clone()

    monkeypatch.setattr(torch.Tensor, "is_cuda", property(lambda self: id(self) not in streamed_ids))
    monkeypatch.setattr(wo, "cast_to", _fake_cast_to)

    def stage_calls_for(tensor):
        return [c for c in calls if c[0] == id(tensor)]

    return stage_calls_for


def _spy(monkeypatch, instance, method_name: str) -> list:
    """Wrap an instance's bound method with a call-recording spy that still
    runs the real implementation, installed as an instance attribute (shadows
    the class method for THIS object only) via ``monkeypatch`` so it is
    reverted automatically at the end of the test."""
    original = getattr(instance, method_name)
    calls: list = []

    def spy_fn(*a, **kw):
        calls.append((a, kw))
        return original(*a, **kw)

    monkeypatch.setattr(instance, method_name, spy_fn)
    return calls


# --- dequant path (gate off / ineligible): prepared once per projection -----

def test_dequant_operand_prepared_once_per_projection_regardless_of_chunk_count(monkeypatch):
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    x = torch.randn(1, 9, 16)

    unchunked_attn = _fp8_attn()
    unchunked_qkv_calls = _spy(monkeypatch, unchunked_attn.qkv_proj, "_prepare_dequant_operand")
    unchunked_out_calls = _spy(monkeypatch, unchunked_attn.out_proj, "_prepare_dequant_operand")
    reference = unchunked_attn(x, None, None, 0)
    assert len(unchunked_qkv_calls) == 1
    assert len(unchunked_out_calls) == 1

    chunked_attn = _fp8_attn()  # fresh instance, identical fixed-seed weights
    chunked_qkv_calls = _spy(monkeypatch, chunked_attn.qkv_proj, "_prepare_dequant_operand")
    chunked_out_calls = _spy(monkeypatch, chunked_attn.out_proj, "_prepare_dequant_operand")
    # 9 rows, chunk=4 -> chunks of 4, 4, 1 (ragged tail exercised too).
    chunked = chunked_attn(x, None, None, seq_chunk_rows=4)

    assert len(chunked_qkv_calls) == 1   # NOT 3 -- once per projection, not per chunk
    assert len(chunked_out_calls) == 1
    assert torch.allclose(chunked, reference, atol=1e-6)


def test_dequant_preparation_failure_propagates_and_subsequent_call_recovers(monkeypatch):
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    attn = _fp8_attn()
    x = torch.randn(1, 9, 16)
    reference = attn(x, None, None, 0)

    original = attn.qkv_proj._prepare_dequant_operand

    def _boom(dtype, device):
        raise RuntimeError("simulated preparation failure")

    monkeypatch.setattr(attn.qkv_proj, "_prepare_dequant_operand", _boom)
    with pytest.raises(RuntimeError, match="simulated preparation failure"):
        attn(x, None, None, seq_chunk_rows=4)

    # Nothing left half-prepared or cached from the failed attempt: restoring
    # the real method makes the SAME instance work correctly again.
    monkeypatch.setattr(attn.qkv_proj, "_prepare_dequant_operand", original)
    recovered = attn(x, None, None, seq_chunk_rows=4)
    assert torch.allclose(recovered, reference, atol=1e-6)


def test_changed_weight_between_calls_is_never_stale_cached(monkeypatch):
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    attn = _fp8_attn()
    x = torch.randn(1, 9, 16)
    first = attn(x, None, None, seq_chunk_rows=4)

    _load_fp8(attn.qkv_proj, attn.qkv_proj.out_features, attn.qkv_proj.in_features, seed=99)
    second = attn(x, None, None, seq_chunk_rows=4)

    # There is no cross-context cache to invalidate (prepared_linear computes
    # from current state every time this loop runs) -- a changed weight must
    # be reflected immediately, never a stale operand from the first call.
    assert not torch.allclose(first, second, atol=1e-3)


# --- ordinary (non-quantised) layer: explicit pass-through control ----------

def test_ordinary_float_layer_prepared_linear_is_a_true_passthrough():
    attn = _float_attn()
    x = torch.randn(1, 9, 16)
    with attn.qkv_proj.prepared_linear(x) as proj:
        assert proj is attn.qkv_proj  # no wrapper object, no extra state at all

    with torch.no_grad():
        reference = attn(x, None, None, 0)
        chunked = attn(x, None, None, seq_chunk_rows=4)
    # The attention core itself (never chunked either way) has a tiny
    # kernel-order-dependent float32 tolerance of its own -- see
    # test_attention_oom_retries_query_chunked_sdpa_and_matches_the_dense_output
    # in test_minimax_h3_model.py for the same allowance; the point here is
    # the passthrough control (no reuse machinery to prove), not attention
    # bit-exactness.
    assert torch.allclose(chunked, reference, atol=1e-5, rtol=1e-4)


# --- fp8 GEMM fast path (opt-in gate on): staged once, kernel still per-chunk

def _fp8_stage_calls(stage_calls_for, tensor) -> list:
    """Just the fp8-dtype (staging) casts of ``tensor``'s calls -- excludes a
    bf16-dtype dequant cast of the SAME tensor, which a per-chunk fallback
    (a kernel rejection, see the next test) can legitimately also produce."""
    return [c for c in stage_calls_for(tensor) if c[1] == torch.float8_e4m3fn]


def test_fp8_fast_path_stages_weight_once_but_kernel_runs_once_per_chunk(monkeypatch):
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    stage_calls_for = _install_fake_cuda_residency(
        monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight],
    )
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    # bf16: the fast path's dtype precondition (float16/bfloat16 only) -- the
    # layer itself stays fp32-registered, only the activation dtype matters.
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    kernel_calls: list = []

    def _fake_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        kernel_calls.append(1)
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype)

    with patch.object(wo, "_scaled_mm_supported", return_value=True), \
         patch("torch._scaled_mm", side_effect=_fake_scaled_mm):
        attn(x, None, None, seq_chunk_rows=4)  # chunks of 4, 4, 1

    # ONE staging copy per projection, regardless of 3 chunks each.
    assert len(_fp8_stage_calls(stage_calls_for, attn.qkv_proj.weight)) == 1
    assert len(_fp8_stage_calls(stage_calls_for, attn.out_proj.weight)) == 1
    # The kernel itself still runs once per chunk per projection (3 + 3):
    # only the WEIGHT preparation is amortised, never the chunk-dependent
    # input quantisation / GEMM.
    assert len(kernel_calls) == 6


def test_fp8_fast_path_kernel_rejection_on_one_chunk_falls_back_for_that_chunk_only(monkeypatch):
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    stage_calls_for = _install_fake_cuda_residency(
        monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight],
    )
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    qkv_dequant_calls = _spy(monkeypatch, attn.qkv_proj, "forward_comfy_cast_weights")

    # qkv_proj's loop runs first (3 chunks -> kernel calls starting at 1), so
    # rejecting calls #2 AND #3 hits exactly the middle chunk's own attempt
    # AND its fallback's retry (forward_comfy_cast_weights re-decides fresh
    # and would otherwise self-heal on a transient one-shot failure) --
    # forcing a genuine rejection for that one chunk, while leaving the third
    # chunk's call (#4, reusing the loop's shared operand) to succeed.
    call_n = {"n": 0}

    def _flaky_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        call_n["n"] += 1
        if call_n["n"] in (2, 3):
            raise RuntimeError("simulated kernel-level rejection")
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype)

    with patch.object(wo, "_scaled_mm_supported", return_value=True), \
         patch("torch._scaled_mm", side_effect=_flaky_scaled_mm):
        out = attn(x, None, None, seq_chunk_rows=4)

    # The loop's own prepared operand was staged for the whole loop (chunks 0
    # and 2 reuse it with no staging of their own -- proven by test above);
    # the rejected middle chunk's OWN fallback dispatch (a normal, independent
    # single-call attempt, same as the non-chunked contract) is the only
    # source of additional staging here.
    assert len(_fp8_stage_calls(stage_calls_for, attn.qkv_proj.weight)) >= 1
    # Exactly the rejected chunk fell back to the ordinary per-call dispatch.
    assert len(qkv_dequant_calls) == 1
    assert out.shape == (1, 9, 16)


def test_fp8_fast_path_staging_failure_falls_back_to_dequant_for_the_whole_projection(monkeypatch):
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    # bf16 and _scaled_mm_supported patched below, so every OTHER fast-path
    # precondition holds -- isolates the staging failure itself as the reason
    # dequant runs, not an unrelated ineligibility (dtype, device).
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    # Simulate an on-demand-staging OOM for BOTH projections: the fast path is
    # unavailable for the ENTIRE loop, decided once at context-entry, not
    # re-attempted per chunk (out_proj is forced too so its own, otherwise
    # eligible, fast path doesn't reach the kernel and mask the assertion).
    monkeypatch.setattr(attn.qkv_proj, "_stage_scaled_mm_weight", lambda device: None)
    monkeypatch.setattr(attn.out_proj, "_stage_scaled_mm_weight", lambda device: None)
    dequant_calls = _spy(monkeypatch, attn.qkv_proj, "_prepare_dequant_operand")

    with patch.object(wo, "_scaled_mm_supported", return_value=True), \
         patch("torch._scaled_mm", side_effect=AssertionError("kernel must not run when staging failed")):
        attn(x, None, None, seq_chunk_rows=4)

    # ONE dequant preparation for the whole loop -- not one per chunk, and the
    # kernel (asserted via the side_effect above) was never reached.
    assert len(dequant_calls) == 1
