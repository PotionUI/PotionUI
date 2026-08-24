# Forked from diffusers 0.39.0's transformer_krea2.py
# (https://github.com/huggingface/diffusers/blob/v0.39.0/src/diffusers/models/transformers/transformer_krea2.py),
# Copyright 2026 Krea AI and The HuggingFace Team. Licensed Apache-2.0
# (http://www.apache.org/licenses/LICENSE-2.0).
#
# Local modifications (this file replaces a Wan2GP-derived port, which
# is deleted, not kept alongside):
#   * every module attribute is named to match the NATIVE checkpoint's keys
#     (`wq`/`wk`/`wv`/`wo`/`gate`, `qknorm.qnorm`/`qknorm.knorm`, `mod.lin` as a
#     raw Parameter, `prenorm`/`postnorm`, `layerwise_blocks`/`refiner_blocks`,
#     ...) instead of diffusers' (`to_q`/`to_k`/`to_v`/`to_out`/`to_gate`,
#     `norm_q`/`norm_k`, `scale_shift_table`, `norm1`/`norm2`,
#     `layerwise_blocks`/`refiner_blocks` — those two already matched). Zero
#     checkpoint remap: the 430-key real-header fixture parity test is the gate.
#   * every `nn.Linear` is the injected `operations.Linear` (the cast-weight-op
#     seam: low-VRAM streaming, fp8/nvfp4 quantised load, and prefetch all key
#     off this identity — a raw `nn.Linear` here would silently break them for
#     a quantised checkpoint). `Krea2AttnProcessor`'s pluggable-processor
#     indirection is collapsed directly into `Attention.forward` — we have one
#     attention implementation, not diffusers' swappable-backend registry.
#   * RMSNorm casts its WEIGHT to the activation dtype instead of upcasting the
#     activation to fp32 (diffusers' `Krea2RMSNorm.forward` does the latter).
#     Numerically equivalent to within bf16 rounding, but `F.rms_norm` only
#     dispatches to its fused kernel when input and weight dtypes match — an
#     f32 weight against a bf16 input falls to the ~12x-slower unfused path.
#     Verified bit-for-bit against the prior implementation this file
#     replaces (see `tests/.../test_krea2_edit.py`'s HEAD-exec comparison).
#   * `SingleStreamBlock`/`DoubleSharedModulation` cast the F32 modulation
#     vector to the residual (activation) dtype at the point of application,
#     which diffusers' `Krea2TransformerBlock.forward` does not do. Diffusers
#     gets away with skipping this because its own `_keep_in_fp32_modules`
#     doesn't cover `scale_shift_table`/`time_mod_proj`, so in its usual (all-
#     bf16) loading path this table is bf16 too and no promotion ever happens.
#     OUR checkpoint stores the per-block modulation table (`mod.lin`) as raw
#     F32 (a format fact, not an implementation choice) — porting diffusers'
#     formula verbatim against that F32 tensor would promote every residual
#     add to F32, defeat bf16 tensor cores, and reintroduce the exact
#     3.45x-slower bug this cast fixes. Not optional; re-verified against our
#     real checkpoint's per-tensor dtypes before writing this file.
#   * rotary embeddings use the same interleaved 2x2-rotation-matrix convention
#     as `vendor/gpl/comfyui/flux/math_ops.py` (ComfyUI/Flux lineage) rather
#     than diffusers' `get_1d_rotary_pos_embed`/`apply_rotary_emb`. RoPE has no
#     learned parameters, so an equivalent-but-different application
#     convention would silently corrupt every generation on a real checkpoint
#     (the weights were trained against ONE specific convention) while still
#     "loading" fine — too high-stakes to swap for diffusers' convention
#     without a GPU-validated equivalence proof, which is out of scope here.
#     This is NOT Wan2GP-original expression despite living in the file the
#     old header called a Wan2GP "faithful port" — Wan2GP itself inherited the
#     convention from the Flux/ComfyUI lineage:
#       - `rope()` is proven bit-exact (`torch.equal`, CPU, several dim/theta/
#         ntk combinations — see `test_krea2_rope_matches_vendored_flux_rope`)
#         against `vendor/gpl/comfyui/flux/math_ops.rope`, given `ntk` folds
#         algebraically into `theta` (`1/((theta*ntk)**scale) ==
#         1/(theta_eff**scale)` for `theta_eff = theta*ntk`) — so `rope()` is
#         now a thin wrapper over the vendored function rather than a
#         reimplementation, with `ntk` composed at the call site instead of
#         forking a whole function for one extra parameter.
#       - `apply_rope_inplace`/`ropeapply` are NOT bit-exact against the
#         vendored `math_ops.apply_rope1`
#         (`test_krea2_apply_rope_inplace_vs_vendored_flux_not_bitexact`
#         proves the divergence, up to ~1.5e-2 at bf16) because they
#         deliberately skip the fp32 upcast the vendored function does
#         (`x.to(dtype=freqs_cis.dtype)`) and mutate in place instead of
#         allocating a new output tensor — a genuine perf variant of the SAME
#         rotation math, not a different convention, so it stays its own
#         function (attributed to the vendored Flux/ComfyUI convention, not
#         Wan2GP) rather than being imported outright.
#   * `SwiGLU`'s `multiple=128`-rounding constructor formula is native-engine
#     bookkeeping (our shape-sniffing detector reverse-derives a `multiplier`
#     from the checkpoint's observed `mlp.gate.weight` row count, and this
#     formula must round-trip back to the SAME row count when the module is
#     built) — diffusers has no analogous need since it reads a resolved
#     `intermediate_size` directly from a config file we don't have. Kept.
#   * `temb()` is a free function (no learned params of its own — `tmlp` is the
#     MLP that follows it) but is expression-identical to diffusers'
#     `Krea2TimestepEmbedding.forward`'s sinusoidal half (same `half = dim//2`,
#     same `t.float() * 1e3` pre-multiply, same `[:, None, None]` broadcast
#     shape, same cos-then-sin concat order) — checked side-by-side against
#     the installed diffusers source. Despite living in the file the old
#     header called a Wan2GP "faithful port", this formula is diffusers-
#     identical, not a Wan2GP expression that happens to coincide.
#   * `attention` is our own native seam (`src/platform/runtime/native/attention.py`,
#     `attention()`), not diffusers' `dispatch_attention_fn`. `_sdpa_gqa`'s
#     kv-head repeat_interleave-before-attention is the only standard way to
#     bridge GQA (unequal q/kv head counts) onto an attention primitive that
#     assumes equal head counts — diffusers instead handles this inside its
#     own backend dispatch (`enable_gqa=True`), which isn't a shape our seam
#     accepts. This is integration glue for OUR seam's contract, carrying no
#     distinctive Wan2GP expression to attribute or replace.
#   * FBCache's block-0 probe/skip seam (BE-?, `run_blocks` in model.py) and
#     the ref_latents in-context edit hook (model.py) are additive to
#     our sampler/plugin integration; diffusers' upstream carries neither.
#
# No Wan2GP-derived expression remains in this file: every function/class
# above traces to either diffusers 0.39.0 (Apache-2.0, this file's primary
# fork source) or `vendor/gpl/comfyui/flux/math_ops.py` (GPL-3.0, imported by
# reference per the vendoring policy — see `vendor/NOTICE.md`), with the
# specific mapping recorded next to each. Parameter/layer names are the
# native checkpoint's, unchanged — this is what the 430-key real-header
# fixture parity test enforces.

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from torch import Tensor

from ...attention import attention as _dispatch_attention
from ...nag import apply_nag
from vendor.gpl.comfyui.flux.math_ops import rope as _flux_rope


def _nag_active(nag: dict | None) -> bool:
    return bool(nag) and float(nag.get("scale", 1.0)) > 1.0


def rope(pos: Tensor, dim: int, theta: float = 1e4, ntk: float = 1.0) -> Tensor:
    """Thin wrapper over the vendored Flux/ComfyUI RoPE builder
    (``vendor/gpl/comfyui/flux/math_ops.rope``). ``ntk`` folds algebraically
    into ``theta`` (``1/((theta*ntk)**scale) == 1/(theta_eff**scale)`` for
    ``theta_eff = theta*ntk``), so composing it here at the call site is
    exactly equivalent to the old fork's extra multiply — proven bit-exact
    (``torch.equal``) by ``test_krea2_rope_matches_vendored_flux_rope``."""
    return _flux_rope(pos, dim, theta * ntk)


def apply_rope_inplace(x: Tensor, freqs: Tensor) -> Tensor:
    """In-place variant of the vendored Flux/ComfyUI rotation
    (``vendor/gpl/comfyui/flux/math_ops.apply_rope1``): same 2x2-rotation-
    matrix convention (``cos``/``sin`` read straight off the matrix's first
    column, matching ``apply_rope1``'s column-vector formulation), but
    mutates ``x`` in place and casts the (small) ``cos``/``sin`` tensors down
    to the activation dtype instead of upcasting the (large) activation
    tensor to the freqs dtype — a memory/perf variant of the SAME math, not a
    different convention. NOT bit-exact against the vendored out-of-place
    function (``test_krea2_apply_rope_inplace_vs_vendored_flux_not_bitexact``
    proves the divergence, up to ~1.5e-2 at bf16, entirely attributable to
    the different cast order), so it is kept as its own function rather than
    imported."""
    freqs = freqs[:, None, :, :, :]
    cos = freqs[..., 0, 0].to(x.dtype)
    sin = freqs[..., 1, 0].to(x.dtype)
    x_pair = x.reshape(*x.shape[:-1], -1, 2)
    x0 = x_pair[..., 0].clone()
    x1 = x_pair[..., 1]
    x_pair[..., 0].mul_(cos).sub_(x1 * sin)
    x_pair[..., 1].mul_(cos).add_(x0 * sin)
    return x


def ropeapply(xq: Tensor, xk: Tensor, freqs: Tensor) -> tuple[Tensor, Tensor]:
    """Applies :func:`apply_rope_inplace` to both q and k — the two-tensor
    convenience wrapper diffusers' ``Krea2AttnProcessor.__call__`` inlines at
    its call site instead of naming separately."""
    return apply_rope_inplace(xq, freqs), apply_rope_inplace(xk, freqs)


def temb(t: Tensor, dim: int, period: float = 1e4, tfactor: float = 1e3,
         device: torch.device | None = None, dtype: torch.dtype | None = None) -> Tensor:
    """Sinusoidal flow-time embedding — matches diffusers'
    ``Krea2TimestepEmbedding.forward``'s cos-first, ``t * 1000`` convention;
    computed as a free function instead of an ``nn.Module`` since (unlike
    diffusers) it carries no parameters of its own — ``tmlp`` is the MLP that
    follows it."""
    half = dim // 2
    freqs = torch.exp(-math.log(period) * torch.arange(half, dtype=torch.float32, device=device) / half)
    args = (t.float() * tfactor)[:, None, None] * freqs
    sin, cos = torch.sin(args), torch.cos(args)
    return torch.cat((cos, sin), dim=-1).to(dtype=dtype)


# Derived from: github.com/lbouaraba/comfyui-krea2edit __init__.py
# ``_ref_attn_bias`` (Apache-2.0, author lbouaraba) — the additive-log-space
# target->reference attention bias, including the MASK branch (the
# scalar-only port landed first; region-restricting the boost to part of a ref
# was scoped for follow-up there and lands here).
def ref_attn_bias(boosts: list[float], txt_len: int, ref_lens: list[int], tgt_len: int,
                  device: torch.device, dtype: torch.dtype,
                  ref_grids: list[tuple[int, int]] | None = None,
                  boost_mask: Tensor | None = None) -> Tensor | None:
    """Additive attention-logit bias over the ``[text | refs... | target]``
    sequence: for each reference ``i`` whose ``boosts[i] != 1.0``, the target
    rows attending to that ref's key columns get ``log(boost)`` added to their
    pre-softmax logits — equivalent to multiplying that ref's post-softmax
    attention weight by ``boost`` before renormalization (a reference-fidelity
    dial). ``boosts`` is aligned with the ref blocks in sequence order (last
    entry = last ref = the subject, by the edit workflow's convention).

    ``boost_mask`` (region ref_boost, matching upstream's MASK branch
    exactly): restricts the LAST ref's boost (index ``len(boosts) - 1`` — the
    subject, the same ref ``ref_boost`` targets) to a subset of its columns.
    Any-shape float tensor (e.g. ``(h, w)``, ``(1, h, w)`` or ``(1, 1, h,
    w)``, values expected pre-normalized to roughly ``[0, 1]`` and already fit
    to that ref's own pixel geometry by the caller) is area-interpolated to
    ``ref_grids[-1]`` (that ref's own token grid — hence ``ref_grids`` is
    required whenever ``boost_mask`` is given) and thresholded at ``> 0.5``:
    columns where the resized mask exceeds the threshold get ``log(boost)``;
    every other column of that ref gets NO bias at all (0.0 — behaves as if
    that ref's boost were the neutral ``1.0``), never a partial/soft weight.
    Ignored for every ref except the last, and ignored entirely when the
    last ref's own boost is exactly ``1.0`` (that ref is skipped before the
    mask is ever consulted, same as the plain scalar path).

    Returns ``None`` when every boost is exactly ``1.0`` — the caller MUST keep
    the fast (no-bias) attention path in that case, since any non-None bias
    forces torch SDPA and disables the accelerated sage/flash backends. The
    returned tensor is ``(1, 1, L, L)`` (broadcasts over batch and heads).
    """
    if all(b == 1.0 for b in boosts):
        return None
    offs = [txt_len]
    for rl in ref_lens:
        offs.append(offs[-1] + rl)
    rows0 = offs[-1]
    length = rows0 + tgt_len
    bias = torch.zeros(1, 1, length, length, device=device, dtype=dtype)
    n = len(boosts)
    for i, b in enumerate(boosts):
        if b == 1.0:
            continue
        off, rl = offs[i], ref_lens[i]
        if boost_mask is not None and i == n - 1 and ref_grids is not None:
            gh, gw = ref_grids[i]
            m = boost_mask
            while m.ndim < 4:
                m = m[None]
            m = F.interpolate(m[:1, :1].float(), size=(gh, gw), mode="area")[0, 0]
            cols = off + torch.nonzero(m.reshape(-1) > 0.5, as_tuple=True)[0].to(device)
            bias[:, :, rows0:, cols] = math.log(max(b, 1e-4))
        else:
            bias[:, :, rows0:, off:off + rl] = math.log(max(b, 1e-4))
    return bias


def _sdpa_gqa(q: Tensor, k: Tensor, v: Tensor, heads: int, kvheads: int, mask: Tensor | None) -> Tensor:
    """Attention over head-split tensors with GQA; returns merged ``(B, L, H*D)``.

    ``q`` is ``(B, H, L, D)``, ``k``/``v`` are ``(B, Hkv, L, D)``. kv heads are
    expanded to query heads (repeat_interleave) so the shared attention seam,
    which assumes equal head counts, works unchanged. Diffusers' equivalent
    (``dispatch_attention_fn(..., enable_gqa=...)``) handles GQA inside its own
    backend instead; this model uses the native engine's shared seam, so GQA
    is expanded here at the call site.
    """
    if kvheads != heads:
        repeat = heads // kvheads
        k = k.repeat_interleave(repeat, dim=1)
        v = v.repeat_interleave(repeat, dim=1)
    out = _dispatch_attention(q, k, v, mask=mask)          # (B, H, L, D)
    return rearrange(out, "B H L D -> B L (H D)")


class RMSNorm(nn.Module):
    """Diffusers' ``Krea2RMSNorm``: zero-centered scale, effective multiplier
    ``1 + weight``. Checkpoint key ``*.scale`` (not diffusers' ``*.weight`` —
    renamed for native-checkpoint parity); kept raw float32, per
    ``_keep_in_fp32_modules`` in the diffusers source."""

    def __init__(self, features: int, eps: float = 1e-5, device=None):
        super().__init__()
        self.features = features
        self.eps = eps
        self.scale = nn.Parameter(torch.zeros(features, device=device, dtype=torch.float32))

    def forward(self, x: Tensor) -> Tensor:
        dtype = x.dtype
        # Cast the (f32) scale to the activation dtype rather than upcasting x
        # to f32 (diffusers' `hidden_states.float()`) -- F.rms_norm only
        # dispatches to its fused kernel when input and weight dtypes match;
        # an f32 weight against a bf16 input falls to the ~12x-slower unfused
        # path. Values are identical within bf16 rounding tolerance.
        out = F.rms_norm(x, (self.features,), eps=self.eps, weight=(self.scale + 1.0).to(dtype))
        return out.to(dtype)


class SimpleModulation(nn.Module):
    """Diffusers' ``Krea2FinalLayer``'s inline ``scale_shift_table`` (2, dim)
    add-then-chunk, split into its own module (checkpoint key
    ``last.modulation.lin``) so ``LastLayer`` can share the same shape as
    ``DoubleSharedModulation`` below."""

    def __init__(self, dim: int, device=None):
        super().__init__()
        self.lin = nn.Parameter(torch.zeros(2, dim, device=device, dtype=torch.float32))
        self.multiplier = 2

    def forward(self, vec: Tensor) -> tuple[Tensor, Tensor]:
        # f32 scale/shift (self.lin + f32 vec); the caller (LastLayer.forward)
        # casts them to the activation dtype at the point of application.
        out = vec + rearrange(self.lin, "two d -> 1 two d")
        scale, shift = out.chunk(self.multiplier, dim=1)
        return scale, shift


class DoubleSharedModulation(nn.Module):
    """Diffusers' ``Krea2TransformerBlock``'s per-block ``scale_shift_table``
    (6, hidden_size) — renamed ``mod.lin`` (checkpoint key) and pulled out of
    the block so the residual-dtype cast (below) has one place to live."""

    def __init__(self, dim: int, device=None):
        super().__init__()
        self.lin = nn.Parameter(torch.zeros(6 * dim, device=device, dtype=torch.float32))

    def forward(self, vec: Tensor) -> tuple[Tensor, ...]:
        # self.lin is a raw f32 parameter and ``vec`` (the timestep modulation
        # vector) is itself f32, so these scale/shift/gate tensors are f32. The
        # CALLER casts them to the activation dtype before applying them (see
        # SingleStreamBlock.forward) -- that is where the residual dtype is
        # known. Diffusers' upstream skips this cast; see the file header for
        # why that would reintroduce the f32-residual perf bug on OUR
        # checkpoint (its own modulation table is raw f32, unlike diffusers'
        # usual all-bf16 loading).
        out = vec + self.lin
        return out.chunk(6, dim=-1)


class PositionalEncoding(nn.Module):
    """Diffusers' ``Krea2RotaryPosEmbed``, same 3-axis (t, h, w) split, using
    the vendored Flux/ComfyUI interleaved rotation-matrix convention (see
    :func:`rope` and the file header) rather than diffusers'
    ``get_1d_rotary_pos_embed``/``apply_rotary_emb``."""

    def __init__(self, axdims: list[int], theta: float = 1e2, ntk: float = 1.0):
        super().__init__()
        self.axdims = axdims
        self.theta = theta
        self.ntk = ntk

    def forward(self, pos: Tensor) -> Tensor:
        return torch.cat([rope(pos[..., i], d, self.theta, self.ntk) for i, d in enumerate(self.axdims)], dim=-3)


class QKNorm(nn.Module):
    """Diffusers keeps ``norm_q``/``norm_k`` as separate ``Krea2Attention``
    attributes; the native checkpoint nests them under one ``qknorm`` — kept
    as its own module purely so the checkpoint key (``attn.qknorm.qnorm.scale``
    / ``attn.qknorm.knorm.scale``) round-trips."""

    def __init__(self, dim: int):
        super().__init__()
        self.qnorm = RMSNorm(dim)
        self.knorm = RMSNorm(dim)


class SwiGLU(nn.Module):
    """Diffusers' ``Krea2SwiGLU`` (``gate``/``up``/``down``, same names —
    matched the checkpoint already). ``mlpdim``'s ``multiple=128`` rounding is
    native-engine bookkeeping with no diffusers equivalent: our shape-sniffing
    detector reverse-derives ``multiplier`` from the checkpoint's observed
    ``mlp.gate.weight`` row count, and this formula must round-trip to that
    SAME row count when the module is built (diffusers instead reads a
    resolved ``intermediate_size`` straight from a config file we don't have)."""

    def __init__(self, features: int, multiplier: int, bias: bool = False, multiple: int = 128,
                 operations=None, dtype=None, device=None):
        super().__init__()
        mlpdim = int(2 * features / 3) * multiplier
        mlpdim = multiple * ((mlpdim + multiple - 1) // multiple)
        self.gate = operations.Linear(features, mlpdim, bias=bias, dtype=dtype, device=device)
        self.up = operations.Linear(features, mlpdim, bias=bias, dtype=dtype, device=device)
        self.down = operations.Linear(mlpdim, features, bias=bias, dtype=dtype, device=device)
        self.features = features
        self.mlpdim = mlpdim

    def forward(self, x: Tensor) -> Tensor:
        return self.down(F.silu(self.gate(x)) * self.up(x))


class Attention(nn.Module):
    """Diffusers' ``Krea2Attention`` + ``Krea2AttnProcessor`` collapsed into
    one module (we have one attention implementation, not a pluggable-backend
    registry). Attribute renames for checkpoint parity: ``to_q``/``to_k``/
    ``to_v``/``to_gate``/``to_out[0]`` -> ``wq``/``wk``/``wv``/``gate``/``wo``;
    ``norm_q``/``norm_k`` -> ``qknorm.qnorm``/``qknorm.knorm``. Forward
    sequence (projections -> qk-norm -> rope -> attention -> flatten ->
    sigmoid-gate multiply -> output projection) matches
    ``Krea2AttnProcessor.__call__`` exactly; head-split layout is ``(B, H, L,
    D)`` (rather than diffusers' ``(B, L, H, D)``) because that is what our
    attention seam (``_sdpa_gqa``) expects.
    """

    def __init__(self, dim: int, heads: int, kvheads: int | None = None, bias: bool = False,
                 operations=None, dtype=None, device=None):
        super().__init__()
        self.heads = heads
        self.kvheads = kvheads if kvheads is not None else heads
        self.headdim = dim // self.heads
        self.wq = operations.Linear(dim, self.headdim * self.heads, bias=bias, dtype=dtype, device=device)
        self.wk = operations.Linear(dim, self.headdim * self.kvheads, bias=bias, dtype=dtype, device=device)
        self.wv = operations.Linear(dim, self.headdim * self.kvheads, bias=bias, dtype=dtype, device=device)
        self.gate = operations.Linear(dim, dim, bias=bias, dtype=dtype, device=device)
        self.qknorm = QKNorm(self.headdim)
        self.wo = operations.Linear(dim, dim, bias=bias, dtype=dtype, device=device)

    def forward(self, x: Tensor, freqs: Tensor | None = None, mask: Tensor | None = None,
                nag_ctx: Tensor | None = None, nag_freqs: Tensor | None = None,
                nag_mask: Tensor | None = None, nag: dict | None = None,
                txt_len: int | None = None) -> Tensor:
        q = rearrange(self.wq(x), "B L (H D) -> B H L D", H=self.heads)
        k = rearrange(self.wk(x), "B L (H D) -> B H L D", H=self.kvheads)
        v = rearrange(self.wv(x), "B L (H D) -> B H L D", H=self.kvheads)
        q = self.qknorm.qnorm(q)
        k = self.qknorm.knorm(k)
        if freqs is not None:
            q, k = ropeapply(q, k, freqs)
        out = _sdpa_gqa(q, k, v, self.heads, self.kvheads, mask)
        # NAG (arXiv:2505.21179; mirrors src/platform/runtime/native/arch/wan/model.py's
        # WanT2VCrossAttention and arch/ltx/model.py's CrossAttention). Krea-2's block is
        # JOINT self-attention over ``[text | (refs) | image]`` rather than a separate
        # cross-attention module, so there is no static K/V projection to swap: instead
        # the SAME (already rope'd) image-token queries are re-attended against a second
        # K/V built from ``[nag_ctx | same image tokens]`` -- ``nag_ctx`` is the negative
        # prompt run through this block's own prenorm/modulation (see
        # ``SingleStreamBlock.forward``), so only the text portion of the key/value set
        # changes between the two passes. Blend happens on the raw attention output,
        # before the sigmoid gate and ``wo`` projection -- same point Wan/LTX blend at.
        if nag_ctx is not None and _nag_active(nag):
            img_len = x.shape[1] - txt_len
            q_img = q[:, :, -img_len:, :]
            x_neg = torch.cat([nag_ctx, x[:, -img_len:]], dim=1)
            k_neg = rearrange(self.wk(x_neg), "B L (H D) -> B H L D", H=self.kvheads)
            v_neg = rearrange(self.wv(x_neg), "B L (H D) -> B H L D", H=self.kvheads)
            k_neg = self.qknorm.knorm(k_neg)
            if nag_freqs is not None:
                k_neg = apply_rope_inplace(k_neg, nag_freqs)
            neg_img = _sdpa_gqa(q_img, k_neg, v_neg, self.heads, self.kvheads, nag_mask)
            pos_img = out[:, -img_len:, :]
            blended = apply_nag(pos_img, neg_img, nag["scale"], nag.get("tau", 3.5), nag.get("alpha", 0.5))
            out = torch.cat([out[:, :-img_len, :], blended], dim=1)
        gate = F.sigmoid(self.gate(x))
        out = out * gate
        return self.wo(out)


class LastLayer(nn.Module):
    """Diffusers' ``Krea2FinalLayer``: adaptive RMSNorm + output projection.
    ``scale_shift_table`` -> ``modulation`` (a ``SimpleModulation``, checkpoint
    key ``last.modulation.lin``)."""

    def __init__(self, features: int, patch: int, channels: int, operations=None, dtype=None, device=None):
        super().__init__()
        self.norm = RMSNorm(features)
        self.linear = operations.Linear(features, patch * patch * channels, bias=True, dtype=dtype, device=device)
        self.modulation = SimpleModulation(features)

    def forward(self, x: Tensor, tvec: Tensor) -> Tensor:
        scale, shift = self.modulation(tvec)
        dt = x.dtype
        x = self.norm(x)
        x = x * (scale.to(dt) + 1) + shift.to(dt)
        return self.linear(x)


class TextFusionBlock(nn.Module):
    """Diffusers' ``Krea2TextFusionBlock``: pre-norm attention + SwiGLU, no
    rotary embeddings, no time modulation. ``norm1``/``norm2`` ->
    ``prenorm``/``postnorm`` for checkpoint parity."""

    def __init__(self, features: int, heads: int, multiplier: int, bias: bool = False,
                 kvheads: int | None = None, operations=None, dtype=None, device=None):
        super().__init__()
        self.prenorm = RMSNorm(features)
        self.postnorm = RMSNorm(features)
        self.attn = Attention(features, heads, kvheads, bias, operations=operations, dtype=dtype, device=device)
        self.mlp = SwiGLU(features, multiplier, bias, operations=operations, dtype=dtype, device=device)

    def forward(self, x: Tensor, mask: Tensor | None = None) -> Tensor:
        x = x + self.attn(self.prenorm(x), mask=mask)
        x = x + self.mlp(self.postnorm(x))
        return x


class TextFusionTransformer(nn.Module):
    """Diffusers' ``Krea2TextFusion``: ``layerwise_blocks`` attend across the
    tapped text-encoder-layer axis per token, ``projector`` (a ``Linear
    (num_text_layers, 1)``) collapses that axis, ``refiner_blocks`` attend
    across the token sequence. Same attribute names as diffusers already —
    only the sub-block internals (``Attention``/``SwiGLU`` above) are renamed."""

    def __init__(self, num_txt_layers: int, txt_dim: int, heads: int, multiplier: int, bias: bool = False,
                 kvheads: int | None = None, operations=None, dtype=None, device=None):
        super().__init__()
        self.layerwise_blocks = nn.ModuleList(
            [TextFusionBlock(txt_dim, heads, multiplier, bias, kvheads, operations=operations, dtype=dtype, device=device) for _ in range(2)]
        )
        self.projector = operations.Linear(num_txt_layers, 1, bias=False, dtype=dtype, device=device)
        self.refiner_blocks = nn.ModuleList(
            [TextFusionBlock(txt_dim, heads, multiplier, bias, kvheads, operations=operations, dtype=dtype, device=device) for _ in range(2)]
        )

    def forward(self, x: Tensor, mask: Tensor | None = None) -> Tensor:
        b, l, n, d = x.shape
        x = x.reshape(b * l, n, d)
        for block in self.layerwise_blocks:
            x = block(x.contiguous(), mask=None)
        x = rearrange(x, "(b l) n d -> b l d n", b=b, l=l)
        x = self.projector(x).squeeze(-1)
        for block in self.refiner_blocks:
            x = block(x, mask=mask)
        return x


class SingleStreamBlock(nn.Module):
    """Diffusers' ``Krea2TransformerBlock``: one shared modulation vector
    (``temb``) plus a per-block learned table splits into 6 scale/shift/gate
    tensors, applied around pre-norm attention and pre-norm SwiGLU.
    ``scale_shift_table`` -> ``mod`` (``DoubleSharedModulation``, checkpoint
    key ``mod.lin``); ``norm1``/``norm2`` -> ``prenorm``/``postnorm``.
    """

    def __init__(self, features: int, heads: int, multiplier: int, bias: bool = False,
                 kvheads: int | None = None, operations=None, dtype=None, device=None):
        super().__init__()
        self.mod = DoubleSharedModulation(features)
        self.prenorm = RMSNorm(features)
        self.postnorm = RMSNorm(features)
        self.attn = Attention(features, heads, kvheads, bias, operations=operations, dtype=dtype, device=device)
        self.mlp = SwiGLU(features, multiplier, bias, operations=operations, dtype=dtype, device=device)

    def forward(self, x: Tensor, vec: Tensor, freqs: Tensor, mask: Tensor | None = None,
                nag_fused: Tensor | None = None, nag_freqs: Tensor | None = None,
                nag_mask: Tensor | None = None, nag: dict | None = None,
                txt_len: int | None = None) -> Tensor:
        # The modulation vector ``vec`` (timestep-derived) is f32 and the f32
        # ``mod.lin`` keeps the scale/shift/gate f32. Applying them directly
        # would promote the bf16 residual ``x`` to f32, and since Krea-2 loads
        # under manual_cast ops every DiT Linear would then cast its bf16
        # weight UP to f32 and run on the ~3.8x-slower f32 SIMT path instead
        # of bf16 tensor cores -- the dominant per-step cost. Cast the
        # modulation to the residual (compute) dtype so the whole stream
        # stays bf16 (diffusers' upstream has no equivalent cast — see the
        # file header for why porting it verbatim would reintroduce this).
        dt = x.dtype
        prescale, preshift, pregate, postscale, postshift, postgate = (m.to(dt) for m in self.mod(vec))
        h = self.prenorm(x) * (prescale + 1) + preshift
        # NAG (see Attention.forward above): ``nag_fused`` is the STATIC fused
        # negative-text representation (never evolved through blocks, matching
        # Wan/LTX's static uncond context); re-normalize/modulate it with THIS
        # block's own prenorm/scale/shift every call, exactly as the positive
        # text prefix embedded in ``x`` is. RMSNorm + affine modulation act
        # per-token on the feature axis only, so this is equivalent to
        # normalizing the concatenated [nag_fused | image] sequence directly.
        nag_ctx = None
        if nag_fused is not None and _nag_active(nag):
            nag_ctx = self.prenorm(nag_fused.to(dt)) * (prescale + 1) + preshift
        x = x + self.attn(h, freqs, mask, nag_ctx=nag_ctx, nag_freqs=nag_freqs,
                           nag_mask=nag_mask, nag=nag, txt_len=txt_len) * pregate
        h = self.postnorm(x) * (postscale + 1) + postshift
        x = x + self.mlp(h) * postgate
        return x
