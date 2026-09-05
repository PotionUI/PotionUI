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

import gc
import weakref
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


def _computing_scaled_mm(calls: list):
    """A fake ``torch._scaled_mm`` that COMPUTES a nonzero result from the
    actual operands it receives -- unlike ``torch.zeros(...)``, a wrong or
    zeroed staged operand changes this result, so an output comparison
    against it actually proves something about VALUES, not just shapes."""
    def fake(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        calls.append(1)
        out = (a.to(torch.float32) * scale_a.to(torch.float32)) @ (b.to(torch.float32) * scale_b.to(torch.float32))
        if bias is not None:
            out = out + bias.to(torch.float32)
        return out.to(out_dtype)
    return fake


def test_fp8_fast_path_kernel_rejection_downgrades_whole_projection_to_dequant(monkeypatch):
    """A kernel-level rejection releases the loop's staged weight/scale
    BEFORE any fallback allocation and permanently downgrades the REST of
    that projection's chunks (the rejected one included) to a single dequant
    operand -- it is never retried against the fast path again in this
    context (see prepared_linear's docstring). Rejecting the very FIRST
    kernel call of qkv_proj's loop makes this unambiguous: every one of its 3
    chunks must go through dequant, while out_proj's own (separate) loop,
    untouched, stays on the fast path."""
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    qkv_dequant_prep_calls = _spy(monkeypatch, attn.qkv_proj, "_prepare_dequant_operand")

    kernel_calls: list = []
    real_kernel = _computing_scaled_mm(kernel_calls)

    def _flaky_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        if len(kernel_calls) == 0:  # reject only the very first attempt
            kernel_calls.append(1)
            raise RuntimeError("simulated kernel-level rejection")
        return real_kernel(a, b, scale_a=scale_a, scale_b=scale_b, out_dtype=out_dtype, bias=bias)

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _flaky_scaled_mm)
    with torch.no_grad():
        out = attn(x, None, None, seq_chunk_rows=4)

    # ONE dequant preparation for the whole downgraded loop, not once per
    # remaining chunk (2 more chunks after the rejected one).
    assert len(qkv_dequant_prep_calls) == 1
    # qkv_proj's kernel is never retried after the rejection (1 call, then
    # dequant for all 3 chunks); out_proj's own 3 chunks stay on the fast
    # path untouched -- 4 real kernel invocations total, not 6.
    assert len(kernel_calls) == 4

    # Value check, not just shape: the downgraded qkv_proj must match a
    # fully-dequantised (gate off) reference exactly (dequant math is
    # deterministic and identical either way); rebuild the model with the
    # SAME weights since the one under test now carries fast-path mock state.
    monkeypatch.delenv(NATIVE_FP8_MATMUL_ENV, raising=False)
    ref_attn = _fp8_attn()
    with torch.no_grad():
        dense_reference = ref_attn(x, None, None, seq_chunk_rows=4)
    torch.testing.assert_close(out.float(), dense_reference.float(), rtol=0.2, atol=0.15)


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


# --- prefetch ownership: never a silent hook bypass -------------------------

def test_prepared_linear_bails_to_passthrough_when_leaf_has_forward_hooks(monkeypatch):
    """A leaf under prefetch ownership (LayerPrefetcher installs its own
    pre/post forward hooks per streamed leaf -- memory/partial.py) must NOT
    have its chunks routed through a prepared/cached operand: calling
    _forward_scaled_mm/F.linear directly never goes through
    nn.Module.__call__, so those hooks (execution-order recording, consume/
    stage-successor/restore) would silently never fire. A hooked leaf keeps
    going through the ordinary per-chunk self(x_chunk) dispatch instead --
    proven here by a plain forward-pre-hook (standing in for LayerPrefetcher's
    own, without importing memory/partial.py into this ops-level test) that
    fires once per chunk only if `proj(x_chunk) is attn.qkv_proj(x_chunk)`,
    i.e. only if dispatch actually goes through `__call__`."""
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    hook_calls: list = []
    attn.qkv_proj.register_forward_pre_hook(lambda mod, args: hook_calls.append(1))

    dequant_prep_calls = _spy(monkeypatch, attn.qkv_proj, "_prepare_dequant_operand")
    stage_calls = _spy(monkeypatch, attn.qkv_proj, "_stage_scaled_mm_weight")

    kernel_calls: list = []

    def _fake_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        kernel_calls.append(1)
        return torch.zeros(a.shape[0], b.shape[1], dtype=out_dtype)

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _fake_scaled_mm)
    with torch.no_grad():
        attn(x, None, None, seq_chunk_rows=4)  # 3 chunks

    # The hook fired once per chunk -> dispatch genuinely went through
    # nn.Module.__call__ for every chunk, never bypassing it.
    assert len(hook_calls) == 3
    # Ordinary per-chunk dispatch: staged/prepared ONCE would be the
    # optimisation this hooked leaf must NOT get -- it re-decides every
    # chunk, exactly like a leaf never wrapped in prepared_linear at all.
    assert len(stage_calls) == 3
    assert len(dequant_prep_calls) == 0  # eligible + staged every time, no dequant needed
    # qkv_proj's 3 chunks (unamortised, hooked) + out_proj's own 3 chunks
    # (amortised, NOT hooked -- unaffected by qkv_proj's hook) both reach the
    # kernel: 6 real GEMM calls total, not 3.
    assert len(kernel_calls) == 6


def test_prepared_linear_resumes_amortising_once_hooks_are_removed(monkeypatch):
    """Once whatever attached the hooks removes them (e.g. a real
    ModuleStreamer.teardown()), the SAME leaf goes back to the amortised
    prepared path -- the bail is scoped to "currently hooked", not a
    permanent downgrade."""
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    handle = attn.qkv_proj.register_forward_pre_hook(lambda mod, args: None)
    handle.remove()  # hooks gone before the forward even runs

    stage_calls = _spy(monkeypatch, attn.qkv_proj, "_stage_scaled_mm_weight")
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", lambda a, b, **kw: torch.zeros(a.shape[0], b.shape[1], dtype=kw["out_dtype"]))
    with torch.no_grad():
        attn(x, None, None, seq_chunk_rows=4)

    assert len(stage_calls) == 1  # amortised again: staged once for the whole loop


# --- numeric parity: prepared chunked path vs. the original per-chunk path -

def test_fp8_fast_path_prepared_chunked_matches_original_per_chunk_dispatch_by_value(monkeypatch):
    """The prepared path must be numerically transparent: chunking qkv_proj
    through prepared_linear must produce the SAME values as calling the real,
    unmodified per-chunk dispatch (self.qkv_proj(x_chunk) for each chunk,
    exactly what ran before this feature existed) -- using a kernel fake that
    COMPUTES from its actual operands, so a wrong or zeroed staged operand
    would show up as a real numeric mismatch, not just a shape match."""
    attn = _fp8_attn()
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)
    chunks = list(x.split(4, dim=1))  # 4, 4, 1 -- ragged tail

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)

    original_calls: list = []
    monkeypatch.setattr(torch, "_scaled_mm", _computing_scaled_mm(original_calls))
    with torch.no_grad():
        original_per_chunk = torch.cat([attn.qkv_proj(c) for c in chunks], dim=1)

    prepared_calls: list = []
    monkeypatch.setattr(torch, "_scaled_mm", _computing_scaled_mm(prepared_calls))
    with torch.no_grad(), attn.qkv_proj.prepared_linear(x) as proj:
        prepared_chunked = torch.cat([proj(c) for c in chunks], dim=1)

    assert len(original_calls) == 3   # the real per-chunk path: 1 kernel call per chunk
    assert len(prepared_calls) == 3   # the prepared path: same -- only staging is amortised
    torch.testing.assert_close(prepared_chunked.float(), original_per_chunk.float(), rtol=1e-4, atol=1e-4)


# --- staged-operand lifetime: weakref-tracked, never retained past release --

def _spy_stage_with_weakrefs(monkeypatch, leaf) -> list:
    """Wrap `leaf`'s `_stage_scaled_mm_weight` to record a weakref (never the
    tensor itself) of every genuinely-staged (non-resident) result."""
    refs: list[weakref.ReferenceType] = []
    real_stage = wo.Fp8ScaledLinear._stage_scaled_mm_weight

    def _spy(self, device):
        result = real_stage(self, device)
        if self is leaf and result is not None and result is not self.weight:
            refs.append(weakref.ref(result))
        return result

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_stage_scaled_mm_weight", _spy)
    return refs


def test_staged_operand_weakref_dies_at_the_attention_core_boundary(monkeypatch):
    """qkv_proj's prepared context exits (and its staged weight is released)
    BEFORE the attention core ever runs -- _chunked_qkv's `with` block closes
    before returning q/k/v to `forward`, strictly before `sparse_attention`/
    `_dispatch_attention` is reached."""
    attn = _fp8_attn()
    _stub_attention_core(monkeypatch)
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight, attn.out_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)

    refs = _spy_stage_with_weakrefs(monkeypatch, attn.qkv_proj)
    seen_alive_at_dispatch: list[bool] = []

    def _recording_dispatch(q, k, v, ctx):
        gc.collect()
        seen_alive_at_dispatch.append(any(r() is not None for r in refs))
        return None  # dense fallback, same as the real seam on this CPU box

    monkeypatch.setattr(model_module, "sparse_attention", _recording_dispatch)
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _computing_scaled_mm([]))
    with torch.no_grad():
        attn(x, None, None, seq_chunk_rows=4)

    assert refs                                   # a genuine stage did happen
    assert seen_alive_at_dispatch == [False]       # already dead by the attention core


def test_staged_operand_weakref_dies_at_context_exit(monkeypatch):
    attn = _fp8_attn()
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)
    chunks = list(x.split(4, dim=1))

    refs = _spy_stage_with_weakrefs(monkeypatch, attn.qkv_proj)
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _computing_scaled_mm([]))

    with torch.no_grad(), attn.qkv_proj.prepared_linear(x) as proj:
        for c in chunks:
            proj(c)
        gc.collect()
        assert all(r() is not None for r in refs)  # still alive INSIDE the block

    gc.collect()
    assert refs and all(r() is None for r in refs)  # dead once the block exits


def test_staged_operand_weakref_dies_before_dense_fallback_on_kernel_rejection(monkeypatch):
    attn = _fp8_attn()
    _install_fake_cuda_residency(monkeypatch, streamed=[attn.qkv_proj.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")
    x = torch.randn(1, 9, 16, dtype=torch.bfloat16)
    chunks = list(x.split(4, dim=1))

    refs = _spy_stage_with_weakrefs(monkeypatch, attn.qkv_proj)
    dead_before_dequant: list[bool] = []
    real_prepare = wo.Fp8ScaledLinear._prepare_dequant_operand

    def _spy_prepare(self, dtype, device):
        gc.collect()
        dead_before_dequant.append(all(r() is None for r in refs))
        return real_prepare(self, dtype, device)

    monkeypatch.setattr(wo.Fp8ScaledLinear, "_prepare_dequant_operand", _spy_prepare)
    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(
        torch, "_scaled_mm",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("simulated kernel-level rejection")),
    )

    with torch.no_grad(), attn.qkv_proj.prepared_linear(x) as proj:
        for c in chunks:
            proj(c)

    assert refs                                # a genuine stage did happen (then got rejected)
    assert dead_before_dequant == [True]       # dead by the time the dense fallback ran
