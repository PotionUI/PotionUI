"""AWQ ``pre_quant_scale`` activation-smoothing tests.

MiniMax-H3's nvfp4_awq text-encoder repack carries a per-input-channel BF16
``pre_quant_scale`` sidecar on every ``mlp.down_proj``/``self_attn.o_proj``
(50 layers each — verified against ``ai/minimax_h3/te_nvfp4_awq_header.json``).
ModelOpt's AWQ smoothing requires the activation to be multiplied by this scale
BEFORE the quantised matmul; see the provenance note at the top of
``vendor/gpl/comfyui/ops.py`` for the upstream commit this was ported from.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from vendor.gpl.comfyui.ops import dequantize_nvfp4, fp8_ops  # noqa: E402

from ._nvfp4_ref import F4_MAX as _F4_MAX  # noqa: E402
from ._nvfp4_ref import F8_MAX as _F8_MAX  # noqa: E402
from ._nvfp4_ref import quantize_nvfp4  # noqa: E402


def _nvfp4_sd(out_f: int, in_f: int, w: torch.Tensor, pre_quant_scale: torch.Tensor | None):
    pts = (w.abs().amax() / (_F4_MAX * _F8_MAX)).detach()
    packed, block_sw, _, _ = quantize_nvfp4(w, pts)
    sd = {
        "weight": packed,
        "weight_scale": block_sw,
        "weight_scale_2": pts.clone(),
        "comfy_quant": torch.zeros(5, dtype=torch.uint8),
    }
    if pre_quant_scale is not None:
        sd["pre_quant_scale"] = pre_quant_scale
    return sd, pts


def test_nvfp4_linear_consumes_pre_quant_scale_key():
    torch.manual_seed(0)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, _ = _nvfp4_sd(out_f, in_f, w, pqs)

    lin = fp8_ops.Linear(in_f, out_f, bias=False)  # Nvfp4Linear
    missing, unexpected = [], []
    lin._load_from_state_dict(dict(sd), "", {}, True, missing, unexpected, [])
    assert missing == [] and unexpected == []
    assert lin.pre_quant_scale is not None
    assert torch.equal(lin.pre_quant_scale, pqs)


def test_nvfp4_linear_applies_pre_quant_scale_to_the_activation():
    """The forward output must equal ``F.linear(input * pre_quant_scale, dequant_weight)``
    — the scale multiplies the ACTIVATION, not the weight."""
    torch.manual_seed(1)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, pts = _nvfp4_sd(out_f, in_f, w, pqs)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    x = torch.randn(3, in_f, dtype=torch.bfloat16)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f).to(torch.bfloat16)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), dequant_w, None)
    got = lin(x)
    assert torch.allclose(got.float(), expected.float(), atol=1e-3, rtol=1e-3)


def test_nvfp4_linear_pre_quant_scale_is_load_bearing():
    """Bite-check: an UNSCALED reference (weight applied to the raw activation,
    skipping the AWQ smoothing multiply) must differ from the real output —
    otherwise this test structurally cannot catch a regression that silently
    drops ``pre_quant_scale``."""
    torch.manual_seed(2)
    out_f, in_f = 32, 64
    w = torch.randn(out_f, in_f) * 0.03
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    sd, pts = _nvfp4_sd(out_f, in_f, w, pqs)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])

    x = torch.randn(3, in_f, dtype=torch.bfloat16)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f).to(torch.bfloat16)
    unscaled = torch.nn.functional.linear(x, dequant_w, None)
    got = lin(x)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-3, rtol=1e-3)


def test_nvfp4_linear_without_pre_quant_scale_key_is_unaffected():
    """A checkpoint with no AWQ sidecar (every other nvfp4 layer in the wild)
    must load and run exactly as before — no ``pre_quant_scale`` means no
    activation scaling."""
    torch.manual_seed(3)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f) * 0.03
    sd, pts = _nvfp4_sd(out_f, in_f, w, None)
    packed, block_sw = sd["weight"], sd["weight_scale"]

    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin._load_from_state_dict(dict(sd), "", {}, True, [], [], [])
    assert lin.pre_quant_scale is None

    x = torch.randn(3, in_f)
    dequant_w = dequantize_nvfp4(packed, block_sw, pts, out_f, in_f)
    expected = torch.nn.functional.linear(x, dequant_w, None)
    assert torch.allclose(lin(x), expected)


def test_fp8_scaled_linear_also_applies_pre_quant_scale():
    """The base Fp8ScaledLinear path (fp8-scaled, non-nvfp4) applies the same
    mechanism — upstream loads ``pre_quant_scale`` generically per-layer, not
    only for the nvfp4 format."""
    torch.manual_seed(4)
    out_f, in_f = 16, 32
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    q = w.to(torch.float8_e4m3fn)
    scale = torch.tensor(1.0)
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75

    lin = fp8_ops.Linear(in_f, out_f, bias=False)  # Nvfp4Linear, falls through to Fp8ScaledLinear
    lin.comfy_cast_weights = True
    lin._load_from_state_dict(
        {"weight": q, "weight_scale": scale, "pre_quant_scale": pqs}, "", {}, True, [], [], [],
    )
    assert not lin._is_nvfp4
    assert lin.pre_quant_scale is not None

    x = torch.randn(2, in_f, dtype=torch.bfloat16)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)
    got = lin(x)
    assert torch.allclose(got.float(), expected.float(), atol=1e-2, rtol=1e-2)


# --- prepared_linear (chunked callers, e.g. MiniMax-H3's seq_chunk_rows) ----
#
# forward_comfy_cast_weights applies pre_quant_scale unconditionally at ITS
# OWN entry point -- prepared_linear's chunk runners call _forward_scaled_mm/
# F.linear directly (never re-entering forward_comfy_cast_weights per chunk),
# so a regression here can silently skip the AWQ smoothing for every chunked
# caller while a single-call test (above) stays green. These tests exercise
# prepared_linear itself, chunk by chunk, on both branches and across a
# kernel-level fallback.


def _fp8_scaled_leaf(out_f: int, in_f: int, pqs: torch.Tensor, *, seed: int):
    """``load_state_dict(..., assign=True)``, not a direct ``_load_from_
    state_dict`` call: the latter copies fp8-representable VALUES into the
    constructor's existing float32 parameter in place, leaving
    ``lin.weight.dtype == torch.float32`` -- fine for the dequant-only tests
    above, but the fast-path tests below need a genuinely ``float8_e4m3fn``
    -stored weight (``_scaled_mm_fast_path_reject_reason`` checks
    ``weight_dtype`` for real), which only ``assign=True`` produces."""
    torch.manual_seed(seed)
    w = torch.randn(out_f, in_f, dtype=torch.bfloat16) * 0.03
    q = w.to(torch.float8_e4m3fn)
    scale = torch.tensor(0.02)
    lin = fp8_ops.Linear(in_f, out_f, bias=False)
    lin.comfy_cast_weights = True
    lin.load_state_dict(
        {"weight": q, "weight_scale": scale, "pre_quant_scale": pqs}, strict=False, assign=True,
    )
    assert not lin._is_nvfp4
    assert lin.weight.dtype == torch.float8_e4m3fn
    return lin, q, scale


def test_prepared_linear_dequant_branch_applies_pre_quant_scale_per_chunk():
    out_f, in_f = 16, 32
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    lin, q, scale = _fp8_scaled_leaf(out_f, in_f, pqs, seed=10)

    x = torch.randn(5, in_f, dtype=torch.bfloat16)  # 5 rows, chunk=2 -> 2,2,1 (ragged tail)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)

    with lin.prepared_linear(x) as proj:  # gate off -> dequant branch
        got = torch.cat([proj(c) for c in x.split(2, dim=0)], dim=0)

    assert torch.allclose(got.float(), expected.float(), atol=1e-2, rtol=1e-2)

    # Load-bearing: an unscaled reference must differ, or this test could
    # never catch a regression that silently drops pre_quant_scale. The
    # earlier atol=1e-2 above is far larger than these values' own
    # magnitude (~1e-3), so it would trivially call ANY two outputs "close"
    # here regardless of scaling -- a near-zero atol with a real rtol makes
    # this a genuine relative-difference check instead.
    unscaled = torch.nn.functional.linear(x, q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-6, rtol=0.05)


def _install_fake_cuda_residency(monkeypatch, *, streamed: list[torch.Tensor]):
    """Minimal id-EXCLUSION CUDA-residency fake for a bounded CPU test (same
    idiom as tests/platform/runtime/native/arch/test_minimax_h3_prepared_linear.py):
    `.is_cuda` defaults True for everything except the named streamed
    weights; `cast_to` always clones on a cast of one of them."""
    import vendor.gpl.comfyui.ops as wo

    streamed_ids = {id(t) for t in streamed}

    def _fake_cast_to(tensor, dtype, device, *, non_blocking=False):
        if tensor is None:
            return None
        if id(tensor) not in streamed_ids:
            return tensor if tensor.device == device and tensor.dtype == dtype else tensor.to(device=device, dtype=dtype)
        return tensor.detach().to(dtype=dtype).clone()

    monkeypatch.setattr(torch.Tensor, "is_cuda", property(lambda self: id(self) not in streamed_ids))
    monkeypatch.setattr(wo, "cast_to", _fake_cast_to)


def test_prepared_linear_fast_branch_applies_pre_quant_scale_per_chunk(monkeypatch):
    import vendor.gpl.comfyui.ops as wo
    from vendor.gpl.comfyui.ops import NATIVE_FP8_MATMUL_ENV

    out_f, in_f = 16, 32  # both multiples of 16 -- _scaled_mm alignment
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    lin, q, scale = _fp8_scaled_leaf(out_f, in_f, pqs, seed=11)
    _install_fake_cuda_residency(monkeypatch, streamed=[lin.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    x = torch.randn(5, in_f, dtype=torch.bfloat16)
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)

    # A dequant fallback would coincidentally match `expected` too (same
    # underlying math) -- the kernel call count is what actually proves the
    # fast branch, not the dequant branch, ran.
    call_n = {"n": 0}

    def _computing_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        call_n["n"] += 1
        out = (a.to(torch.float32) * scale_a.to(torch.float32)) @ (b.to(torch.float32) * scale_b.to(torch.float32))
        return out.to(out_dtype)

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _computing_scaled_mm)

    with lin.prepared_linear(x) as proj:  # gate on, eligible -> fast branch
        got = torch.cat([proj(c) for c in x.split(2, dim=0)], dim=0)

    assert call_n["n"] == 3  # one real kernel call per chunk (2, 2, 1 rows)
    torch.testing.assert_close(got.float(), expected.float(), rtol=0.15, atol=0.1)

    # Load-bearing, but NOT against a plain F.linear reference: the fast
    # branch quantises the ACTIVATION into fp8 too (_quantize_fp8_dynamic),
    # a real source of noise `expected`/a plain "unscaled" F.linear never
    # sees -- comparing against one of those would risk "not close" for the
    # wrong reason (kernel quantisation noise, not a missing pre_quant_scale)
    # and silently prove nothing. Isolate pre_quant_scale as the ONLY
    # variable: recompute through the identical fast-path pipeline with it
    # removed from the layer, then compare.
    lin.pre_quant_scale = None
    with lin.prepared_linear(x) as proj_unscaled:
        unscaled = torch.cat([proj_unscaled(c) for c in x.split(2, dim=0)], dim=0)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-6, rtol=0.05)

    # Load-bearing: values here are tiny (~1e-3), so the atol=0.1 comparison
    # above would call ANY two outputs "close" regardless of scaling -- an
    # unscaled reference must differ under a real (near-zero-atol) tolerance,
    # or this test could never catch a regression that silently drops
    # pre_quant_scale on the fast branch.
    unscaled = torch.nn.functional.linear(x, q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-6, rtol=0.05)


def test_prepared_linear_pre_quant_scale_not_doubled_after_kernel_rejection(monkeypatch):
    """A kernel-level rejection mid-loop downgrades the REST of the chunks
    (the rejected one included) to the dequant branch -- pre_quant_scale must
    still be applied exactly once per chunk, never twice (the downgrade
    re-processes the ORIGINAL, unscaled chunk; it must not re-enter a path
    that scales it again)."""
    import vendor.gpl.comfyui.ops as wo
    from vendor.gpl.comfyui.ops import NATIVE_FP8_MATMUL_ENV

    out_f, in_f = 16, 32
    pqs = torch.rand(in_f, dtype=torch.bfloat16) * 0.5 + 0.75
    lin, q, scale = _fp8_scaled_leaf(out_f, in_f, pqs, seed=12)
    _install_fake_cuda_residency(monkeypatch, streamed=[lin.weight])
    monkeypatch.setenv(NATIVE_FP8_MATMUL_ENV, "on")

    x = torch.randn(5, in_f, dtype=torch.bfloat16)  # chunks of 2, 2, 1
    expected = torch.nn.functional.linear(x * pqs.to(x.dtype), q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)

    call_n = {"n": 0}

    def _flaky_scaled_mm(a, b, *, scale_a, scale_b, out_dtype, bias=None):
        call_n["n"] += 1
        if call_n["n"] == 1:
            raise RuntimeError("simulated kernel-level rejection")
        return (a.to(torch.float32) * scale_a.to(torch.float32)) @ (b.to(torch.float32) * scale_b.to(torch.float32))

    monkeypatch.setattr(wo, "_scaled_mm_supported", lambda: True)
    monkeypatch.setattr(torch, "_scaled_mm", _flaky_scaled_mm)

    with lin.prepared_linear(x) as proj:
        got = torch.cat([proj(c) for c in x.split(2, dim=0)], dim=0)

    # The very first kernel call was rejected -> the whole loop downgraded to
    # dequant immediately -> the kernel is never reached again.
    assert call_n["n"] == 1
    torch.testing.assert_close(got.float(), expected.float(), rtol=1e-2, atol=1e-4)

    # Load-bearing (see the fast-branch test above for why atol must be
    # near-zero at this value magnitude): an unscaled reference must differ,
    # proving pre_quant_scale really was applied -- once -- to the
    # downgraded (dequant) path too, not silently dropped.
    unscaled = torch.nn.functional.linear(x, q.to(torch.bfloat16) * scale.to(torch.bfloat16), None)
    assert not torch.allclose(got.float(), unscaled.float(), atol=1e-6, rtol=0.05)
