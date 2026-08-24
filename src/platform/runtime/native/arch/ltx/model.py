# Derived from: comfy/ldm/lightricks (ComfyUI, GPL-3.0) for the LTX-2 base
# forward, and diffusers v0.39's transformer_ltx2.py / pipelines/ltx2/
# connectors.py (Apache-2.0) for the LTX-2.3-specific additions (see the
# module docstring below for exactly which), plus diffusers PR #14447
# (Apache-2.0, unreleased at fork time) for the LTX-2.5 construction deltas
# (ff_bias/audio_ff_bias, use_prompt_adaln_single, use_keyframes_abs_pos_
# embedding — no forward-path changes beyond what those flags gate). rope.py
# and patchifier.py moved to vendor/gpl/comfyui/ltx/ verbatim — zero src dependencies, no
# judgment call needed. This class stays in src: NAG (arXiv:2505.21179,
# PotionUI's own re-derivation, not ComfyUI's or diffusers') is threaded as
# explicit forward-signature parameters through CrossAttention and
# BasicAVTransformerBlock, so — same as wan's model.py — there is no clean
# ComfyUI-only boundary to extract; it also extends NativeArchModule and
# orchestrates FBCache.

"""LTX-2 / 2.3 audio-video DiT — ``LTXAVModel`` (``NativeArchModule``).

Vendored from ComfyUI ``comfy/ldm/lightricks`` (model.py ``LTXVModel`` +
av_model.py ``LTXAVModel`` + embeddings_connector.py). Construction is complete
and key-parity-exact against the real checkpoints; the AV **forward** (compressed
per-frame timestep, split/interleaved RoPE, causal temporal positioning, AV
cross-attention, video/audio split, dual FFN) is implemented here against the
ComfyUI 19b reference. GPU golden validation (against the LTX CausalVideoAutoencoder
+ Gemma3-12B TE) remains for the generator slice. The LTX-2.3 paths (2·sigmoid
gated attention, 9-row scale_shift_tables with query-side text-CA modulation,
sigma-driven prompt-adaLN on the text KV side, cross-timestep AV modulation,
per-stream conditioning chain) are implemented against the diffusers v0.39
reference (``models/transformers/transformer_ltx2.py`` + ``pipelines/ltx2/
connectors.py``) and shape-verified against the real 22B checkpoint header;
numeric golden validation on GPU is still pending.

Module layout (matches the real ``model.diffusion_model.*`` keys 1:1):

  video stream : patchify_proj, adaln_single, caption_projection,
                 transformer_blocks[N], scale_shift_table[2,4096], norm_out (no
                 params), proj_out.
  audio stream : audio_patchify_proj, audio_adaln_single, audio_caption_projection,
                 audio_scale_shift_table[2,2048], audio_norm_out (no params),
                 audio_proj_out.
  AV cross     : av_ca_{video_scale_shift, a2v_gate, audio_scale_shift, v2a_gate}
                 _adaln_single (coeffs 4/1/4/1).
  connectors   : video_embeddings_connector, audio_embeddings_connector
                 (learnable_registers[128,3840] + 2x BasicTransformerBlock1D).

Each ``transformer_blocks`` entry is a ``BasicAVTransformerBlock``: video
attn1(self)+attn2(cross)+ff, audio_attn1+attn2+ff, audio_to_video_attn +
video_to_audio_attn, and four scale-shift tables (6/6/5/5).
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from vendor.gpl.comfyui.ltx import rope
from vendor.gpl.comfyui.ltx.patchifier import AudioPatchifier, SymmetricPatchifier, latent_to_pixel_coords
from vendor.gpl.comfyui.ltx.rope import CompressedTimestep, apply_rotary_emb

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from ...nag import apply_nag
from .config import LTXAVConfig


def _nag_active(nag: dict | None) -> bool:
    return bool(nag) and float(nag.get("scale", 1.0)) > 1.0


def _rms_norm(x: Tensor, eps: float = 1e-6) -> Tensor:
    """Unweighted RMS norm (ComfyUI ``comfy.ldm.common_dit.rms_norm`` with no weight)."""
    return F.rms_norm(x, (x.shape[-1],), eps=eps)


def _timestep_embedding(timesteps: Tensor, dim: int = 256, max_period: int = 10000) -> Tensor:
    """Sinusoidal timestep embedding (PixArt ``Timesteps``: flip_sin_to_cos, shift 0)."""
    half = dim // 2
    exponent = -math.log(max_period) * torch.arange(0, half, dtype=torch.float32, device=timesteps.device)
    exponent = exponent / half
    emb = timesteps[:, None].float() * torch.exp(exponent)[None, :]
    emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
    # flip_sin_to_cos=True -> (cos, sin) ordering
    emb = torch.cat([emb[:, half:], emb[:, :half]], dim=-1)
    if dim % 2 == 1:
        emb = F.pad(emb, (0, 1, 0, 0))
    return emb


def _ltx_attention(q: Tensor, k: Tensor, v: Tensor, heads: int, mask: Tensor | None = None) -> Tensor:
    """Attention on flattened ``(B, L, H*D)`` q/k/v via the engine dispatcher.

    Mirrors ComfyUI ``optimized_attention(..., heads=)``: head-split after the
    (already q/k-normed + RoPE'd) projection, dispatch, then merge heads back.
    """
    b = q.shape[0]

    def split(t: Tensor) -> Tensor:
        return t.view(b, t.shape[1], heads, t.shape[-1] // heads).transpose(1, 2)

    out = _dispatch_attention(split(q), split(k), split(v), heads=heads, mask=mask)
    return out.transpose(1, 2).reshape(b, q.shape[1], -1)


class _TimestepEmbedder(nn.Module):
    def __init__(self, in_channels: int, time_embed_dim: int, operations, dtype=None, device=None):
        super().__init__()
        self.linear_1 = operations.Linear(in_channels, time_embed_dim, bias=True, dtype=dtype, device=device)
        self.act = nn.SiLU()
        self.linear_2 = operations.Linear(time_embed_dim, time_embed_dim, bias=True, dtype=dtype, device=device)

    def forward(self, sample: Tensor) -> Tensor:
        return self.linear_2(self.act(self.linear_1(sample)))


class _CombinedTimestepEmbeddings(nn.Module):
    """PixArtAlphaCombinedTimestepSizeEmbeddings (no additional conditions)."""

    def __init__(self, embedding_dim: int, operations, dtype=None, device=None):
        super().__init__()
        # time_proj (sinusoidal) carries no parameters.
        self.timestep_embedder = _TimestepEmbedder(256, embedding_dim, operations, dtype=dtype, device=device)

    def forward(self, timestep: Tensor, hidden_dtype) -> Tensor:
        return self.timestep_embedder(_timestep_embedding(timestep, 256).to(hidden_dtype))


class AdaLayerNormSingle(nn.Module):
    def __init__(self, embedding_dim: int, embedding_coefficient: int, operations, dtype=None, device=None):
        super().__init__()
        self.emb = _CombinedTimestepEmbeddings(embedding_dim, operations, dtype=dtype, device=device)
        self.silu = nn.SiLU()
        self.linear = operations.Linear(embedding_dim, embedding_coefficient * embedding_dim, bias=True, dtype=dtype, device=device)

    def forward(self, timestep: Tensor, hidden_dtype) -> tuple[Tensor, Tensor]:
        """Returns ``(adaLN modulation [·, coeff·D], embedded_timestep [·, D])``."""
        embedded = self.emb(timestep, hidden_dtype)
        return self.linear(self.silu(embedded)), embedded


class PixArtAlphaTextProjection(nn.Module):
    def __init__(self, in_features: int, hidden_size: int, operations, dtype=None, device=None):
        super().__init__()
        self.linear_1 = operations.Linear(in_features, hidden_size, bias=True, dtype=dtype, device=device)
        self.act_1 = nn.GELU(approximate="tanh")
        self.linear_2 = operations.Linear(hidden_size, hidden_size, bias=True, dtype=dtype, device=device)

    def forward(self, caption: Tensor) -> Tensor:
        return self.linear_2(self.act_1(self.linear_1(caption)))


class _GELUApprox(nn.Module):
    def __init__(self, dim_in: int, dim_out: int, operations, bias: bool = True, dtype=None, device=None):
        super().__init__()
        self.proj = operations.Linear(dim_in, dim_out, bias=bias, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return F.gelu(self.proj(x), approximate="tanh")


class FeedForward(nn.Module):
    def __init__(self, dim: int, operations, mult: int = 4, bias: bool = True, dtype=None, device=None):
        super().__init__()
        inner = int(dim * mult)
        self.net = nn.Sequential(
            _GELUApprox(dim, inner, operations, bias=bias, dtype=dtype, device=device),
            nn.Dropout(0.0),
            operations.Linear(inner, dim, bias=bias, dtype=dtype, device=device),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.net(x)


class CrossAttention(nn.Module):
    """LTX cross/self attention with RMSNorm q/k (inner_dim norm, not per-head)."""

    def __init__(self, query_dim: int, heads: int, dim_head: int, operations,
                 context_dim: int | None = None, gate_logits_dim: int | None = None,
                 norm_eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        inner_dim = dim_head * heads
        context_dim = query_dim if context_dim is None else context_dim
        self.heads = heads
        self.dim_head = dim_head
        # q/k RMSNorm eps is config-driven (norm_eps, default 1e-6, matching the
        # LTX 2.3 checkpoint config and diffusers transformer_ltx2.py line 350).
        self.q_norm = operations.RMSNorm(inner_dim, eps=norm_eps, dtype=dtype, device=device)
        self.k_norm = operations.RMSNorm(inner_dim, eps=norm_eps, dtype=dtype, device=device)
        self.to_q = operations.Linear(query_dim, inner_dim, bias=True, dtype=dtype, device=device)
        self.to_k = operations.Linear(context_dim, inner_dim, bias=True, dtype=dtype, device=device)
        self.to_v = operations.Linear(context_dim, inner_dim, bias=True, dtype=dtype, device=device)
        self.to_out = nn.Sequential(
            operations.Linear(inner_dim, query_dim, bias=True, dtype=dtype, device=device),
            nn.Dropout(0.0),
        )
        # LTX-2.3 gated attention: a per-head gate-logit projection off the query
        # stream (out dim is the video head count, 32, everywhere).
        if gate_logits_dim is not None:
            self.to_gate_logits = operations.Linear(query_dim, gate_logits_dim, bias=True, dtype=dtype, device=device)

    def forward(self, x: Tensor, context: Tensor | None = None, mask: Tensor | None = None,
                pe=None, k_pe=None, context_neg: Tensor | None = None, nag: dict | None = None,
                mask_neg: Tensor | None = None) -> Tensor:
        # q/k RMSNorm act on the FULL projection (inner_dim), BEFORE the head split.
        q = self.q_norm(self.to_q(x))
        context = x if context is None else context
        k = self.k_norm(self.to_k(context))
        v = self.to_v(context)
        if pe is not None:
            q = apply_rotary_emb(q, pe)
            k = apply_rotary_emb(k, pe if k_pe is None else k_pe)
        out = _ltx_attention(q, k, v, self.heads, mask=mask)
        # NAG (arXiv:2505.21179): run the same queries against the NEGATIVE
        # context's K/V (through the same to_k/to_v/k_norm weights) and blend
        # on the raw attention output — before the 2.3 gate and before
        # to_out, mirroring Wan's WanT2VCrossAttention pattern
        # (src/platform/runtime/native/arch/wan/model.py). Only text cross-attention call
        # sites (attn2 / audio_attn2) ever pass context_neg; self-attention
        # (attn1/audio_attn1) and the AV cross-attention (audio_to_video_attn/
        # video_to_audio_attn, which attend the video/audio streams to each
        # other, not text) never do, so this stays a no-op there.
        #
        # ``mask`` is the POSITIVE context's padding mask — its shape/values
        # correspond to the positive prompt's real-vs-padding token layout,
        # which is generally NOT the negative prompt's (different token
        # count). Reusing it on the negative K/V would either drop real
        # negative tokens (if the negative prompt is longer/differently
        # padded) or attend negative padding, depending on which prompt is
        # shorter. Use ``mask_neg`` (the negative context's own mask,
        # ``None`` by default) instead — never fall back to ``mask``.
        if context_neg is not None and _nag_active(nag):
            k_neg = self.k_norm(self.to_k(context_neg))
            v_neg = self.to_v(context_neg)
            if pe is not None:
                k_neg = apply_rotary_emb(k_neg, pe if k_pe is None else k_pe)
            neg = _ltx_attention(q, k_neg, v_neg, self.heads, mask=mask_neg)
            out = apply_nag(out, neg, nag["scale"], nag.get("tau", 3.5), nag.get("alpha", 0.5))
        if hasattr(self, "to_gate_logits"):
            # LTX-2.3 gated attention (verified against diffusers transformer_ltx2.py
            # ``LTX2AudioVideoAttnProcessor``, v0.39.0): per-head gate on the attention
            # output BEFORE the out-projection, logits computed from the same
            # (normed + modulated) query-side stream fed to to_q. The 2.0 factor makes
            # zero-initialised gate logits an identity gate (2·σ(0) = 1).
            gate = 2.0 * torch.sigmoid(self.to_gate_logits(x))
            b, lq, _ = out.shape
            out = (out.view(b, lq, self.heads, self.dim_head) * gate.unsqueeze(-1)).reshape(b, lq, -1)
        return self.to_out(out)


class BasicTransformerBlock1D(nn.Module):
    """Embeddings-connector block: self attention (+ optional gate) + feed-forward."""

    def __init__(self, dim: int, dim_head: int, operations, gate_logits_dim: int | None = None,
                 norm_eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        heads = dim // dim_head
        self.attn1 = CrossAttention(dim, heads, dim_head, operations, context_dim=None,
                                    gate_logits_dim=gate_logits_dim, norm_eps=norm_eps,
                                    dtype=dtype, device=device)
        self.ff = FeedForward(dim, operations, dtype=dtype, device=device)

    def forward(self, hidden_states: Tensor, attention_mask: Tensor | None = None, pe=None) -> Tensor:
        norm = _rms_norm(hidden_states)
        if norm.ndim == 4:
            norm = norm.squeeze(1)
        hidden_states = self.attn1(norm, mask=attention_mask, pe=pe) + hidden_states
        if hidden_states.ndim == 4:
            hidden_states = hidden_states.squeeze(1)
        hidden_states = self.ff(_rms_norm(hidden_states)) + hidden_states
        if hidden_states.ndim == 4:
            hidden_states = hidden_states.squeeze(1)
        return hidden_states


class Embeddings1DConnector(nn.Module):
    def __init__(self, inner: int, dim_head: int, num_layers: int, num_learnable_registers: int,
                 operations, gate_logits_dim: int | None = None, norm_eps: float = 1e-6,
                 dtype=None, device=None):
        super().__init__()
        # `heads = inner // dim_head` truncates silently on a bad config pairing
        # (video vs audio connectors are configured independently, see LTXAVConfig)
        # and surfaces far away as a shape error inside `split_freqs_cis` (rope.py).
        # `split_freqs_cis` also requires `(inner // 2) % heads == 0` (an even
        # `dim_head`). Assert both here, before any submodule is constructed, so
        # the failure names the declared inputs.
        derived_heads = inner // dim_head if dim_head else 0
        if dim_head <= 0 or derived_heads * dim_head != inner or dim_head % 2 != 0:
            raise ValueError(
                f"Embeddings1DConnector heads mismatch: declared inner={inner}, dim_head={dim_head} "
                f"-> derived heads={derived_heads} (inner // dim_head); "
                f"{derived_heads} * {dim_head} = {derived_heads * dim_head} != {inner}, "
                f"or dim_head is not even. 'inner' must be an exact multiple of an EVEN "
                f"'dim_head' for the split-RoPE per-head layout to divide cleanly -- check "
                f"connector_attention_head_dim / video_connector_inner (and their audio_ "
                f"counterparts) in LTXAVConfig."
            )
        self.transformer_1d_blocks = nn.ModuleList([
            BasicTransformerBlock1D(inner, dim_head, operations, gate_logits_dim=gate_logits_dim,
                                   norm_eps=norm_eps, dtype=dtype, device=device)
            for _ in range(num_layers)
        ])
        self.learnable_registers = nn.Parameter(torch.empty(num_learnable_registers, inner, dtype=dtype, device=device))
        # Non-parameter scalars needed by the split-RoPE forward (arch constants:
        # theta 10000, max_pos 4096, double-precision freq grid — ComfyUI lt.py).
        self.inner = inner
        self.num_attention_heads = derived_heads
        self.register_count = num_learnable_registers
        # Own cache instance (separate from LTXAVModel's) — the connector's grid
        # (1-D register positions) is a different shape/space than the main
        # video/audio streams, and video vs audio connectors are separate
        # instances, so each already gets its own cache namespace for free.
        self._pe_cache_key: tuple | None = None
        self._pe_cache: tuple | None = None

    def _apply(self, fn, recurse: bool = True):
        self._pe_cache_key = None
        self._pe_cache = None
        return super()._apply(fn, recurse=recurse)

    def _freqs_cis(self, seq_len: int, device, dtype):
        cache_key = (seq_len, str(device), dtype)
        if self._pe_cache_key == cache_key:
            return self._pe_cache
        indices_grid = torch.arange(seq_len, dtype=torch.float32, device=device)[None, None, :]
        indices = rope.generate_freq_grid_np(10000.0, indices_grid.shape[1], self.inner)
        pad = self.inner // 2 - rope.freq_feature_dim(indices, indices_grid.shape[1])
        cos, sin = rope.build_freqs_cis_chunked(
            indices, indices_grid, [4096], pad, dtype,
            split_mode=True, num_attention_heads=self.num_attention_heads,
        )
        result = (cos, sin, True)
        self._pe_cache_key = cache_key
        self._pe_cache = result
        return result

    def forward(self, hidden_states: Tensor, attention_mask: Tensor | None = None):
        """Prepend tiled learnable registers (to ``max(1024, S)``), run the split-RoPE
        1-D blocks, final RMS norm. Returns ``(hidden_states, attention_mask)``."""
        if self.register_count:
            reps = math.ceil(max(1024, hidden_states.shape[1]) / self.register_count)
            regs = torch.tile(self.learnable_registers.to(hidden_states), (reps, 1))
            tail = regs[hidden_states.shape[1]:].unsqueeze(0).repeat(hidden_states.shape[0], 1, 1)
            hidden_states = torch.cat((hidden_states, tail), dim=1)
            if attention_mask is not None:
                attention_mask = torch.zeros([1, 1, 1, hidden_states.shape[1]],
                                             dtype=attention_mask.dtype, device=attention_mask.device)
        pe = self._freqs_cis(hidden_states.shape[1], hidden_states.device, hidden_states.dtype)
        for block in self.transformer_1d_blocks:
            hidden_states = block(hidden_states, attention_mask=attention_mask, pe=pe)
        return _rms_norm(hidden_states), attention_mask


class BasicAVTransformerBlock(nn.Module):
    def __init__(self, v_dim, a_dim, v_heads, a_heads, vd_head, ad_head,
                 v_context_dim, a_context_dim, operations, gate_logits_dim=None,
                 has_prompt_adaln=False, norm_eps=1e-6, ff_bias=True, audio_ff_bias=True,
                 dtype=None, device=None):
        super().__init__()
        gl = gate_logits_dim
        mk = lambda q, h, d, ctx: CrossAttention(q, h, d, operations, context_dim=ctx,
                                                 gate_logits_dim=gl, norm_eps=norm_eps,
                                                 dtype=dtype, device=device)
        # video + audio self / cross attention
        self.attn1 = mk(v_dim, v_heads, vd_head, None)
        self.audio_attn1 = mk(a_dim, a_heads, ad_head, None)
        self.attn2 = mk(v_dim, v_heads, vd_head, v_context_dim)
        self.audio_attn2 = mk(a_dim, a_heads, ad_head, a_context_dim)
        # AV cross attention (Q video / KV audio, and vice-versa)
        self.audio_to_video_attn = mk(v_dim, a_heads, ad_head, a_dim)
        self.video_to_audio_attn = mk(a_dim, a_heads, ad_head, v_dim)
        self.ff = FeedForward(v_dim, operations, bias=ff_bias, dtype=dtype, device=device)
        self.audio_ff = FeedForward(a_dim, operations, bias=audio_ff_bias, dtype=dtype, device=device)
        # 2.3 (cross_attention_adaln) extends the per-block tables 6 -> 9 rows: the
        # extra rows [6:9] are shift/scale/gate for the text cross-attention QUERY
        # side (diffusers transformer_ltx2.py: video_mod_param_num = 9 if adaln).
        # Verified against the 22B header: [9, 4096] / [9, 2048].
        self.has_prompt_adaln = has_prompt_adaln
        mod_rows = 9 if has_prompt_adaln else 6
        self.scale_shift_table = nn.Parameter(torch.empty(mod_rows, v_dim, dtype=dtype, device=device))
        self.audio_scale_shift_table = nn.Parameter(torch.empty(mod_rows, a_dim, dtype=dtype, device=device))
        self.scale_shift_table_a2v_ca_audio = nn.Parameter(torch.empty(5, a_dim, dtype=dtype, device=device))
        self.scale_shift_table_a2v_ca_video = nn.Parameter(torch.empty(5, v_dim, dtype=dtype, device=device))
        # LTX-2.3 prompt-conditioning shift/scale for the text KV side (paired with
        # the model-level prompt_adaln_single, driven by sigma).
        if has_prompt_adaln:
            self.prompt_scale_shift_table = nn.Parameter(torch.empty(2, v_dim, dtype=dtype, device=device))
            self.audio_prompt_scale_shift_table = nn.Parameter(torch.empty(2, a_dim, dtype=dtype, device=device))

    def get_ada_values(self, table: Tensor, batch_size: int, timestep, indices: slice = slice(None, None),
                       ref: Tensor | None = None):
        """adaLN scale/shift/gate slices. ``timestep`` is a ``CompressedTimestep``
        (per-frame video), a plain ``[B, T, n_params·D]`` tensor (audio / cross), or
        ``None`` -- LTX-2.5's ``use_prompt_adaln_single=False`` (KV-cacheable
        cross-attention): the prompt-side table is then timestep-INDEPENDENT, so
        the static per-layer table broadcasts directly (diffusers
        ``temb_prompt is None`` branch, ``transformer_ltx2.py``). ``ref`` supplies
        the dtype/device to cast to in that case (the caller's hidden state --
        the table's own dtype may differ under mixed precision)."""
        if timestep is None:
            r = table if ref is None else ref
            return table[indices].unsqueeze(0).unsqueeze(0).to(device=r.device, dtype=r.dtype).unbind(dim=2)
        if isinstance(timestep, CompressedTimestep):
            return timestep.expand_for_computation(table, batch_size, indices)
        num = table.shape[0]
        return (
            table[indices].unsqueeze(0).unsqueeze(0).to(device=timestep.device, dtype=timestep.dtype)
            + timestep.reshape(batch_size, timestep.shape[1], num, -1)[:, :, indices, :]
        ).unbind(dim=2)

    def forward(self, vx: Tensor, ax: Tensor, v_context=None, a_context=None, attention_mask=None,
                v_timestep=None, a_timestep=None, v_pe=None, a_pe=None, v_cross_pe=None, a_cross_pe=None,
                v_cross_scale_shift_timestep=None, a_cross_scale_shift_timestep=None,
                v_cross_gate_timestep=None, a_cross_gate_timestep=None,
                v_prompt_timestep=None, a_prompt_timestep=None,
                run_vx: bool = True, run_ax: bool = True,
                a2v_cross_attn: bool = True, v2a_cross_attn: bool = True,
                v_context_neg=None, a_context_neg=None, nag=None, nag_attention_mask=None,
                skip_self_attn: bool = False):
        """One AV double-block (ComfyUI ``av_model.BasicAVTransformerBlock`` + the 2.3
        prompt-adaLN paths from diffusers ``transformer_ltx2.py``): video self+cross,
        audio self+cross, then a2v/v2a cross-attention with adaLN scale/shift/gate,
        then the two feed-forwards. With ``has_prompt_adaln`` (2.3), the text
        cross-attention is additionally modulated on BOTH sides: query-side
        shift/scale/gate from rows [6:9] of the widened scale_shift_table (driven by
        the ordinary per-token timestep), and KV-side shift/scale on the text context
        from prompt_scale_shift_table (driven by ``*_prompt_timestep`` = the
        sigma-fed prompt_adaln_single output).

        ``v_context_neg``/``a_context_neg``/``nag``: NAG (see ``CrossAttention.
        forward``'s docstring) on the two TEXT cross-attention sites (``attn2``,
        ``audio_attn2``) only — the a2v/v2a cross-attention below attends the
        video/audio streams to each other, not text, so it never receives these.
        ``None`` (the default) is a no-op at every downstream call site.
        ``nag_attention_mask``: the NEGATIVE context's own padding mask, passed
        as ``attn2``/``audio_attn2``'s ``mask_neg`` — deliberately NEVER the
        positive ``attention_mask`` (the two contexts generally have different
        real-token lengths).

        ``skip_self_attn``: MultiModalGuider STG hook (ported from ltx-core
        SelfAttentionPerturbation, Apache-2.0, rev a2c3f24). When True, self-
        attention output (attn1/audio_attn1) is replaced with the RAW VALUE
        PROJECTION (v = to_v(normalized_input)) passed through the same
        downstream path (gating + to_out + residual) that real attention takes.
        The reference (ltx-core/model/transformer/attention.py lines 217-235,
        rev a2c3f24) computes v = to_v(context), skips q/k entirely, and feeds
        v through the same post-attention path (gating if present, to_out).
        This is NOT zeroing — it's value-passthrough perturbation."""
        run_ax = run_ax and ax.numel() > 0
        run_a2v = run_vx and a2v_cross_attn and ax.numel() > 0
        run_v2a = run_ax and v2a_cross_attn

        if run_vx:
            vshift_msa, vscale_msa = self.get_ada_values(self.scale_shift_table, vx.shape[0], v_timestep, slice(0, 2))
            norm_vx = _rms_norm(vx) * (1 + vscale_msa) + vshift_msa
            if skip_self_attn:
                # STG perturbation: value-projection passthrough (reference:
                # ltx-core/model/transformer/attention.py lines 217-235, rev a2c3f24).
                # When all_perturbed=True, attention.forward computes v = to_v(context)
                # and skips q/k entirely, feeding v through the same post-attention path
                # (gating if present, to_out). Self-attention: context = x (the normalized input).
                v = self.attn1.to_v(norm_vx)
                if hasattr(self.attn1, "to_gate_logits"):
                    gate = 2.0 * torch.sigmoid(self.attn1.to_gate_logits(vx))  # gate logits from PRE-NORM vx
                    b, lq, _ = v.shape
                    v = (v.view(b, lq, self.attn1.heads, self.attn1.dim_head) * gate.unsqueeze(-1)).reshape(b, lq, -1)
                attn1_out = self.attn1.to_out(v)
            else:
                attn1_out = self.attn1(norm_vx, pe=v_pe)
            vgate_msa = self.get_ada_values(self.scale_shift_table, vx.shape[0], v_timestep, slice(2, 3))[0]
            vx = vx + attn1_out * vgate_msa
            norm_vx2 = _rms_norm(vx)
            if self.has_prompt_adaln:
                vshift_tq, vscale_tq = self.get_ada_values(self.scale_shift_table, vx.shape[0], v_timestep, slice(6, 8))
                norm_vx2 = norm_vx2 * (1 + vscale_tq) + vshift_tq
                kv_shift, kv_scale = self.get_ada_values(
                    self.prompt_scale_shift_table, vx.shape[0], v_prompt_timestep, slice(0, 2), ref=vx)
                v_context = v_context * (1 + kv_scale) + kv_shift
                if v_context_neg is not None:
                    v_context_neg = v_context_neg * (1 + kv_scale) + kv_shift
            attn2_out = self.attn2(norm_vx2, context=v_context, mask=attention_mask,
                                    context_neg=v_context_neg, nag=nag, mask_neg=nag_attention_mask)
            if self.has_prompt_adaln:
                attn2_out = attn2_out * self.get_ada_values(
                    self.scale_shift_table, vx.shape[0], v_timestep, slice(8, 9))[0]
            vx = vx + attn2_out

        if run_ax:
            ashift_msa, ascale_msa = self.get_ada_values(self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(0, 2))
            norm_ax = _rms_norm(ax) * (1 + ascale_msa) + ashift_msa
            if skip_self_attn:
                # STG perturbation on audio stream: same v-passthrough as video.
                v = self.audio_attn1.to_v(norm_ax)
                if hasattr(self.audio_attn1, "to_gate_logits"):
                    gate = 2.0 * torch.sigmoid(self.audio_attn1.to_gate_logits(ax))
                    b, lq, _ = v.shape
                    v = (v.view(b, lq, self.audio_attn1.heads, self.audio_attn1.dim_head) * gate.unsqueeze(-1)).reshape(b, lq, -1)
                attn1_out = self.audio_attn1.to_out(v)
            else:
                attn1_out = self.audio_attn1(norm_ax, pe=a_pe)
            agate_msa = self.get_ada_values(self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(2, 3))[0]
            ax = ax + attn1_out * agate_msa
            norm_ax2 = _rms_norm(ax)
            if self.has_prompt_adaln:
                ashift_tq, ascale_tq = self.get_ada_values(self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(6, 8))
                norm_ax2 = norm_ax2 * (1 + ascale_tq) + ashift_tq
                akv_shift, akv_scale = self.get_ada_values(
                    self.audio_prompt_scale_shift_table, ax.shape[0], a_prompt_timestep, slice(0, 2), ref=ax)
                a_context = a_context * (1 + akv_scale) + akv_shift
                if a_context_neg is not None:
                    a_context_neg = a_context_neg * (1 + akv_scale) + akv_shift
            attn2_out = self.audio_attn2(norm_ax2, context=a_context, mask=attention_mask,
                                          context_neg=a_context_neg, nag=nag, mask_neg=nag_attention_mask)
            if self.has_prompt_adaln:
                attn2_out = attn2_out * self.get_ada_values(
                    self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(8, 9))[0]
            ax = ax + attn2_out

        if run_a2v or run_v2a:
            vx_norm3 = _rms_norm(vx)
            ax_norm3 = _rms_norm(ax)

            if run_a2v:  # audio -> video cross attention (Q video, KV audio)
                scale_a_a2v, shift_a_a2v = self.get_ada_values(
                    self.scale_shift_table_a2v_ca_audio[:4, :], ax.shape[0], a_cross_scale_shift_timestep)[:2]
                scale_v_a2v, shift_v_a2v = self.get_ada_values(
                    self.scale_shift_table_a2v_ca_video[:4, :], vx.shape[0], v_cross_scale_shift_timestep)[:2]
                vx_scaled = vx_norm3 * (1 + scale_v_a2v) + shift_v_a2v
                ax_scaled = ax_norm3 * (1 + scale_a_a2v) + shift_a_a2v
                a2v_out = self.audio_to_video_attn(vx_scaled, context=ax_scaled, pe=v_cross_pe, k_pe=a_cross_pe)
                gate_a2v = self.get_ada_values(self.scale_shift_table_a2v_ca_video[4:, :], vx.shape[0], v_cross_gate_timestep)[0]
                vx = vx + a2v_out * gate_a2v

            if run_v2a:  # video -> audio cross attention (Q audio, KV video)
                scale_a_v2a, shift_a_v2a = self.get_ada_values(
                    self.scale_shift_table_a2v_ca_audio[:4, :], ax.shape[0], a_cross_scale_shift_timestep)[2:4]
                scale_v_v2a, shift_v_v2a = self.get_ada_values(
                    self.scale_shift_table_a2v_ca_video[:4, :], vx.shape[0], v_cross_scale_shift_timestep)[2:4]
                ax_scaled = ax_norm3 * (1 + scale_a_v2a) + shift_a_v2a
                vx_scaled = vx_norm3 * (1 + scale_v_v2a) + shift_v_v2a
                v2a_out = self.video_to_audio_attn(ax_scaled, context=vx_scaled, pe=a_cross_pe, k_pe=v_cross_pe)
                gate_v2a = self.get_ada_values(self.scale_shift_table_a2v_ca_audio[4:, :], ax.shape[0], a_cross_gate_timestep)[0]
                ax = ax + v2a_out * gate_v2a

        if run_vx:
            vshift_mlp, vscale_mlp = self.get_ada_values(self.scale_shift_table, vx.shape[0], v_timestep, slice(3, 5))
            ff_out = self.ff(_rms_norm(vx) * (1 + vscale_mlp) + vshift_mlp)
            vgate_mlp = self.get_ada_values(self.scale_shift_table, vx.shape[0], v_timestep, slice(5, 6))[0]
            vx = vx + ff_out * vgate_mlp

        if run_ax:
            ashift_mlp, ascale_mlp = self.get_ada_values(self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(3, 5))
            ff_out = self.audio_ff(_rms_norm(ax) * (1 + ascale_mlp) + ashift_mlp)
            agate_mlp = self.get_ada_values(self.audio_scale_shift_table, ax.shape[0], a_timestep, slice(5, 6))[0]
            ax = ax + ff_out * agate_mlp

        return vx, ax


class LTXAVModel(NativeArchModule):
    """LTX-2/2.3 audio-video DiT (construction + load path; forward is task-deferred)."""

    def __init__(self, config: LTXAVConfig, operations, dtype=None, device=None):
        super().__init__()
        self.config = config
        self.operations = operations
        d, ad = config.inner_dim, config.audio_inner_dim
        opk = dict(operations=operations, dtype=dtype, device=device)

        # --- video common components ---
        # 2.3 (has_prompt_adaln) widens the main adaLN 6 -> 9 coefficients (the extra
        # 3 modulate the text cross-attn query side). 22B header: linear [36864, 4096].
        adaln_coeff = 9 if config.has_prompt_adaln else 6
        self.patchify_proj = operations.Linear(config.in_channels, d, bias=True, dtype=dtype, device=device)
        # LTX-2.5.1+ generated-keyframe checkpoints (construction/load-parity
        # only -- see LTXAVConfig.use_keyframes_abs_pos_embedding).
        if config.use_keyframes_abs_pos_embedding:
            self.keyframes_abs_pos_embedding = nn.Parameter(torch.empty(1, d, dtype=dtype, device=device))
        self.adaln_single = AdaLayerNormSingle(d, adaln_coeff, **opk)
        # LTX-2 has caption_projection; LTX-2.3 drops it (connector-only conditioning).
        if config.has_caption_projection:
            self.caption_projection = PixArtAlphaTextProjection(config.caption_channels, d, **opk)

        if config.is_av:
            self._init_audio(config, dtype, device, opk)

        self.transformer_blocks = nn.ModuleList([self._make_block(config, dtype, device) for _ in range(config.num_layers)])

        # --- video output ---
        self.scale_shift_table = nn.Parameter(torch.empty(2, d, dtype=dtype, device=device))
        self.norm_out = operations.LayerNorm(d, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.proj_out = operations.Linear(d, config.out_channels, bias=True, dtype=dtype, device=device)

        if config.is_av:
            self._init_audio_output(config, dtype, device)

        # --- runtime helpers (no parameters; do not affect state-dict / key parity) ---
        # LTX patch size is 1 (no spatial merge); tokens carry (start, end) coords.
        self.patchifier = SymmetricPatchifier(1, start_end=True)
        if config.is_av:
            self.a_patchifier = AudioPatchifier(1, start_end=True)
        # Load-time contract: every non-VAE arch module exposes ``patch_size``.
        self.patch_size = self.patchifier.patch_size
        self.vae_scale_factors = (8, 32, 32)
        self.num_audio_channels = 8
        self.audio_frequency_bins = 16
        # Both real checkpoints (19b + 2.3) declare use_middle_indices_grid=True
        # in their embedded __metadata__ config: RoPE positions sample the token
        # CENTER of each patch/frame span instead of its start. False here
        # produces a visible grid texture (verified via same-seed A/B).
        self.use_middle_indices_grid = True
        # Per-generation positional-embedding cache (see _prepare_positional_embeddings):
        # the grid shape / frame_rate / dtype / device / split-mode are invariant
        # across a generation's denoise steps, so the RoPE tables only need building
        # once instead of once per forward. Plain instance attributes (not buffers/
        # parameters) so they never enter the state-dict; _apply() below drops them
        # on any device/dtype move (offload, .cuda(), .float(), ...) so a stale
        # GPU-resident table can never survive past the residency it was built for.
        self._pe_cache_key: tuple | None = None
        self._pe_cache: list | None = None

    def _apply(self, fn, recurse: bool = True):
        self._pe_cache_key = None
        self._pe_cache = None
        return super()._apply(fn, recurse=recurse)

    def _init_audio(self, config, dtype, device, opk):
        ad = config.audio_inner_dim
        adaln_coeff = 9 if config.has_prompt_adaln else 6
        self.audio_patchify_proj = self.operations.Linear(config.audio_in_channels, ad, bias=True, dtype=dtype, device=device)
        self.audio_adaln_single = AdaLayerNormSingle(ad, adaln_coeff, **opk)
        # AV-cross-attention scale/shift + gate adaln (coeffs 4/1/4/1).
        self.av_ca_video_scale_shift_adaln_single = AdaLayerNormSingle(config.inner_dim, 4, **opk)
        self.av_ca_a2v_gate_adaln_single = AdaLayerNormSingle(config.inner_dim, 1, **opk)
        self.av_ca_audio_scale_shift_adaln_single = AdaLayerNormSingle(ad, 4, **opk)
        self.av_ca_v2a_gate_adaln_single = AdaLayerNormSingle(ad, 1, **opk)
        if config.has_caption_projection:
            self.audio_caption_projection = PixArtAlphaTextProjection(config.caption_channels, ad, **opk)
        # LTX-2.3 prompt-conditioning adaLN (coeff 2), video + audio. LTX-2.5 can
        # drop this MLP (use_prompt_adaln_single=False) while keeping the
        # per-block prompt_scale_shift_table -- see LTXAVConfig.
        if config.has_prompt_adaln and config.use_prompt_adaln_single:
            self.prompt_adaln_single = AdaLayerNormSingle(config.inner_dim, 2, **opk)
            self.audio_prompt_adaln_single = AdaLayerNormSingle(ad, 2, **opk)
        if config.use_embeddings_connector:
            gate = config.connector_gate_dim if config.connector_gated else None
            common = dict(num_layers=config.connector_num_layers,
                          num_learnable_registers=config.connector_num_learnable_registers,
                          gate_logits_dim=gate, **opk)
            # 2.3's audio connector runs at the audio head dim (64 -> 32 heads); the
            # 19b shared 3840 connector uses 128 for both streams.
            self.video_embeddings_connector = Embeddings1DConnector(
                inner=config.video_connector_inner, dim_head=config.connector_attention_head_dim,
                norm_eps=config.norm_eps, **common)
            self.audio_embeddings_connector = Embeddings1DConnector(
                inner=config.audio_connector_inner, dim_head=config.audio_connector_attention_head_dim,
                norm_eps=config.norm_eps, **common)

    def _init_audio_output(self, config, dtype, device):
        ad = config.audio_inner_dim
        self.audio_scale_shift_table = nn.Parameter(torch.empty(2, ad, dtype=dtype, device=device))
        self.audio_norm_out = self.operations.LayerNorm(ad, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.audio_proj_out = self.operations.Linear(ad, config.audio_in_channels, bias=True, dtype=dtype, device=device)

    def _make_block(self, config, dtype, device):
        if not config.is_av:
            # video-only LTXVModel block would be a plain BasicTransformerBlock; the
            # local checkpoints are all AV, so that path is added when a video-only
            # checkpoint appears.
            raise NotImplementedError("video-only LTXV block not needed for local (all-AV) checkpoints")
        return BasicAVTransformerBlock(
            v_dim=config.inner_dim, a_dim=config.audio_inner_dim,
            v_heads=config.num_attention_heads, a_heads=config.audio_num_attention_heads,
            vd_head=config.attention_head_dim, ad_head=config.audio_attention_head_dim,
            v_context_dim=config.cross_attention_dim, a_context_dim=config.audio_cross_attention_dim,
            operations=self.operations,
            gate_logits_dim=config.block_gate_dim if config.blocks_gated else None,
            has_prompt_adaln=config.has_prompt_adaln,
            norm_eps=config.norm_eps,
            ff_bias=config.ff_bias, audio_ff_bias=config.audio_ff_bias,
            dtype=dtype, device=device,
        )

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "LTXAVModel":
        """Build empty-weight from a detected config dict (wrap in
        ``with torch.device("meta")`` — the DiT is ~19-22B params)."""
        return cls(LTXAVConfig.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """LTX computes RoPE frequency grids and sinusoidal timestep embeddings
        per forward, so there are no derived buffers to recompute (verified:
        the DiT registers no buffers).

        The one load-parity gap: 2.5 checkpoints declare
        ``use_keyframes_abs_pos_embedding`` in their embedded config while not
        every file carries the weight — presence varies by REPACK, not by
        ``model_version`` (the nvfp4 2.5.0 repack drops the tensor; the
        int8-convrot 2.5.0 repack ships a trained one), so the tensor's
        presence is the only reliable signal. A weightless file leaves the
        meta-built parameter unmaterialised after the assign-load. Upstream
        (ltx_core ``model.py``, ``enable_keyframes_abs_pos_embedding``)
        resolves the same gap with real zeros — an exact no-op that never
        overwrites a trained embedding."""
        p = getattr(self, "keyframes_abs_pos_embedding", None)
        if p is not None and p.is_meta:
            self.keyframes_abs_pos_embedding = nn.Parameter(
                torch.zeros(p.shape, dtype=p.dtype, device=self.patchify_proj.weight.device)
            )

    # -- conditioning chain (Gemma3 -> DiT caption context) -----------------

    def apply_text_conditioning(self, gemma_output: Tensor, video_projection_weight: Tensor,
                                audio_projection_weight: Tensor | None = None,
                                video_projection_bias: Tensor | None = None,
                                audio_projection_bias: Tensor | None = None,
                                attention_mask: Tensor | None = None) -> Tensor:
        """RAW Gemma3 stack ``[B, S, 188160]`` -> DiT context ``[B, S, v_ctx + a_ctx]``.

        Takes the UN-normalised, channel-major flattened per-layer Gemma stack
        (``hidden(3840) × layers(49)``) and applies the variant's normalisation here —
        it differs between versions (so it must not be baked into the TE):

        19b (ComfyUI ``LTXAVTEModel.encode_token_weights``): per-(batch, layer)
        min-max/mean norm over (seq, hidden) ×8, one shared bias-less projection
        (188160->3840), shared input to both 2-block 3840 connectors.

        2.3 (diffusers ``pipelines/ltx2/connectors.py LTX2TextConnectors.forward``):
        per-token RMS norm over the hidden dim of each layer, optional mask zeroing,
        per-stream ``sqrt(stream_inner / caption_channels)`` rescale, dual BIASED
        projections (video 188160->4096 / audio 188160->2048), 8-block gated
        connectors. Pass ``audio_projection_weight`` to select this path.

        The projection tensors come from the all-in-one LTX DiT checkpoint
        (``text_embedding_projection.*`` — TOP-LEVEL tensors, not part of this
        module's state-dict): 19b ``aggregate_embed.weight`` (no bias); 2.3
        ``{video,audio}_aggregate_embed.{weight,bias}``.
        """
        c = self.config
        out = gemma_output.float()
        stacked = out.unflatten(-1, (c.caption_channels, -1))          # [B, S, H, L]
        if audio_projection_weight is None:
            # 19b: per-(batch, layer) min-max normalisation over the (seq, hidden) axes.
            denom = stacked.amax(dim=(1, 2), keepdim=True) - stacked.amin(dim=(1, 2), keepdim=True) + 1e-6
            flat = (8.0 * (stacked - stacked.mean(dim=(1, 2), keepdim=True)) / denom).flatten(2, 3)
            v_in = F.linear(flat, video_projection_weight.to(flat))
            a_in = v_in
        else:
            # 2.3: per-token RMS norm over the hidden dim of each layer.
            variance = stacked.pow(2).mean(dim=2, keepdim=True)
            flat = (stacked * torch.rsqrt(variance + 1e-6)).flatten(2, 3)
            if attention_mask is not None:
                flat = flat * attention_mask.to(flat).unsqueeze(-1)
            v_scale = math.sqrt(c.inner_dim / c.caption_channels)
            a_scale = math.sqrt(c.audio_inner_dim / c.caption_channels)
            v_in = F.linear(flat * v_scale, video_projection_weight.to(flat),
                            None if video_projection_bias is None else video_projection_bias.to(flat))
            a_in = F.linear(flat * a_scale, audio_projection_weight.to(flat),
                            None if audio_projection_bias is None else audio_projection_bias.to(flat))
        out_vid = self.video_embeddings_connector(v_in)[0]
        out_audio = self.audio_embeddings_connector(a_in)[0]
        return torch.cat((out_vid, out_audio), dim=-1)

    # -- forward orchestration ---------------------------------------------

    def _precompute_freqs_cis(self, indices_grid, dim, out_dtype, max_pos, num_attention_heads,
                              use_middle_indices_grid=False, split_mode=False):
        n_pos_dims = indices_grid.shape[1]
        indices = rope.generate_freq_grid_np(self.config.positional_embedding_theta, n_pos_dims, dim)
        # Pad size is pure shape arithmetic (feature-dim size, independent of the
        # token count) — computed analytically so the chunked builder below never
        # has to materialize a whole-tensor freqs just to read its last-dim size.
        if split_mode:
            pad_size = dim // 2 - rope.freq_feature_dim(indices, n_pos_dims)
        else:
            pad_size = dim % (2 * n_pos_dims)
        cos, sin = rope.build_freqs_cis_chunked(
            indices, indices_grid, max_pos, pad_size, out_dtype,
            use_middle_indices_grid=use_middle_indices_grid, split_mode=split_mode,
            num_attention_heads=num_attention_heads,
        )
        return cos, sin, split_mode

    def _process_input(self, vx: Tensor, ax: Tensor,
                       extra_video_tokens: Tensor | None = None,
                       extra_video_pixel_coords: Tensor | None = None):
        orig_shape = list(vx.shape)
        vx, latent_coords = self.patchifier.patchify(vx)
        v_pixel_coords = latent_to_pixel_coords(latent_coords, self.vae_scale_factors,
                                                self.config.causal_temporal_positioning)
        if extra_video_tokens is not None:
            # Appended conditioning tokens (keyframes / IC-LoRA references): raw
            # latent-channel tokens ride through the same input projection; their
            # caller-built PIXEL-space coords join the base grid (the temporal axis
            # is in pixel FRAMES — ``_prepare_positional_embeddings`` divides the
            # whole grid by ``frame_rate`` once).
            vx = torch.cat([vx, extra_video_tokens.to(dtype=vx.dtype, device=vx.device)], dim=1)
            v_pixel_coords = torch.cat(
                [v_pixel_coords,
                 extra_video_pixel_coords.to(dtype=v_pixel_coords.dtype, device=v_pixel_coords.device)],
                dim=2,
            )
        vx = self.patchify_proj(vx)
        ax, a_latent_coords = self.a_patchifier.patchify(ax)
        ax = self.audio_patchify_proj(ax)
        return [vx, ax], [v_pixel_coords, a_latent_coords], orig_shape

    def _prepare_timestep(self, timestep, a_timestep, batch_size, hidden_dtype, orig_shape,
                          sigma=None, audio_sigma=None):
        c = self.config
        t_scaled = timestep * c.timestep_scale_multiplier
        a_scaled = a_timestep * c.timestep_scale_multiplier
        t_flat, a_flat = t_scaled.flatten(), a_scaled.flatten()

        v_ts, v_emb = self.adaln_single(t_flat, hidden_dtype)
        # Video tokens are (frame·h·w); all patches of a frame share a timestep.
        # With a per-token timestep whose length isn't a whole number of frames
        # (appended conditioning tokens at non-base spatial dims), CompressedTimestep
        # falls back to its safe per-token path via its own divisibility check.
        v_ppf = orig_shape[3] * orig_shape[4] if orig_shape is not None and len(orig_shape) == 5 else None
        v_ts = CompressedTimestep(v_ts.view(batch_size, -1, v_ts.shape[-1]), v_ppf)
        v_emb = CompressedTimestep(v_emb.view(batch_size, -1, v_emb.shape[-1]), v_ppf)

        # Per-batch scalar sigmas (same ×1000 domain — diffusers passes sigma=timestep).
        # 2.3 uses them for prompt-adaLN and, with use_cross_timestep, to drive each
        # stream's AV-cross adaLN with the OTHER modality's sigma. When the video
        # timestep is a per-token conditioning mask (``t * (1 - mask)``), token 0 may
        # be a fully-conditioned 0 — callers MUST then pass the true schedule
        # ``sigma`` explicitly (diffusers passes ``sigma=timestep`` for the same
        # reason); the token-0 derivation is only the legacy uniform-timestep path.
        if sigma is not None:
            t_sigma = (sigma * c.timestep_scale_multiplier).flatten()
        else:
            t_sigma = t_scaled.reshape(batch_size, -1)[:, 0]
        if audio_sigma is not None:
            a_sigma = (audio_sigma * c.timestep_scale_multiplier).flatten()
        else:
            a_sigma = a_scaled.reshape(batch_size, -1)[:, 0]
        v_ca_t = a_sigma if c.use_cross_timestep else t_flat
        a_ca_t = t_sigma if c.use_cross_timestep else a_flat

        av_factor = c.av_ca_timestep_scale_multiplier / c.timestep_scale_multiplier
        av_a_ss, _ = self.av_ca_audio_scale_shift_adaln_single(a_ca_t, hidden_dtype)
        av_v_ss, _ = self.av_ca_video_scale_shift_adaln_single(v_ca_t, hidden_dtype)
        av_a2v_gate, _ = self.av_ca_a2v_gate_adaln_single(v_ca_t * av_factor, hidden_dtype)
        av_v2a_gate, _ = self.av_ca_v2a_gate_adaln_single(a_ca_t * av_factor, hidden_dtype)
        cross = [
            av_a_ss.view(batch_size, -1, av_a_ss.shape[-1]),
            CompressedTimestep(av_v_ss.view(batch_size, -1, av_v_ss.shape[-1]), v_ppf),
            CompressedTimestep(av_a2v_gate.view(batch_size, -1, av_a2v_gate.shape[-1]), v_ppf),
            av_v2a_gate.view(batch_size, -1, av_v2a_gate.shape[-1]),
        ]

        a_ts, a_emb = self.audio_adaln_single(a_flat, hidden_dtype)
        a_ts = a_ts.view(batch_size, -1, a_ts.shape[-1])
        a_emb = a_emb.view(batch_size, -1, a_emb.shape[-1])

        # 2.3 prompt-adaLN: sigma-driven shift/scale for the per-block text-KV
        # modulation (diffusers ``prompt_modulation``). 2.5's
        # use_prompt_adaln_single=False drops the MLP entirely -- ``prompt``
        # stays None and BasicAVTransformerBlock.get_ada_values falls back to
        # the static per-block table (timestep-independent).
        prompt = None
        if c.has_prompt_adaln and c.use_prompt_adaln_single:
            v_pt, _ = self.prompt_adaln_single(t_sigma, hidden_dtype)
            a_pt, _ = self.audio_prompt_adaln_single(a_sigma, hidden_dtype)
            prompt = [v_pt.view(batch_size, -1, v_pt.shape[-1]),
                      a_pt.view(batch_size, -1, a_pt.shape[-1])]
        return [v_ts, a_ts, cross, prompt], [v_emb, a_emb]

    def _prepare_context(self, context, batch_size, vx, ax, attention_mask=None):
        if self.config.has_caption_projection:
            # 19b: both connector halves are caption_channels (3840) wide.
            v_context, a_context = torch.split(context, context.shape[-1] // 2, dim=-1)
            v_context = self.caption_projection(v_context).view(batch_size, -1, vx.shape[-1])
            a_context = self.audio_caption_projection(a_context).view(batch_size, -1, ax.shape[-1])
        else:
            # 2.3: connector-only conditioning at each stream's own width — the
            # concat is asymmetric (video 4096 + audio 2048), not two equal halves.
            v_context, a_context = torch.split(
                context, [self.config.cross_attention_dim, self.config.audio_cross_attention_dim], dim=-1)
        return [v_context, a_context], attention_mask

    @staticmethod
    def _prepare_attention_mask(attention_mask, x_dtype):
        if attention_mask is not None and not torch.is_floating_point(attention_mask):
            attention_mask = (attention_mask - 1).to(x_dtype).reshape(
                (attention_mask.shape[0], 1, -1, attention_mask.shape[-1])
            ) * torch.finfo(x_dtype).max
        return attention_mask

    def _prepare_positional_embeddings(self, pixel_coords, frame_rate, x_dtype):
        c = self.config
        v_pixel_coords = pixel_coords[0].to(torch.float32)
        v_pixel_coords[:, 0] = v_pixel_coords[:, 0] * (1.0 / frame_rate)
        a_latent_coords = pixel_coords[1]
        # rope_split: LTX-2/2.3 use the split-halves rotary for ALL model streams
        # (interleaved is legacy LTXV-0.9 only). ComfyUI selects this per-checkpoint
        # from ``rope_type``; here it rides on the config flag.
        split = c.rope_split
        # Positional embeddings are a pure function of the grid SHAPE, frame_rate,
        # dtype, device and split-mode (patch/token coordinates are assigned by
        # position, not by the noisy sample's values), and that signature is
        # invariant across a generation's denoise steps — only the noisy input
        # changes step to step, never the grid. Cache the built tables keyed on
        # that signature so construction runs once per generation instead of once
        # per forward; _apply() (device/dtype move = offload/reload) drops the
        # cache so it can never survive into a differently-placed residency.
        cache_key = (
            tuple(v_pixel_coords.shape), tuple(a_latent_coords.shape),
            float(frame_rate), x_dtype, str(v_pixel_coords.device), split,
            self.use_middle_indices_grid,
        )
        if self._pe_cache_key == cache_key:
            return self._pe_cache
        v_pe = self._precompute_freqs_cis(v_pixel_coords, c.inner_dim, x_dtype,
                                          c.positional_embedding_max_pos, c.num_attention_heads,
                                          use_middle_indices_grid=self.use_middle_indices_grid, split_mode=split)
        a_pe = self._precompute_freqs_cis(a_latent_coords, c.audio_inner_dim, x_dtype,
                                          c.audio_positional_embedding_max_pos, c.audio_num_attention_heads,
                                          use_middle_indices_grid=self.use_middle_indices_grid, split_mode=split)
        # AV-cross RoPE uses the token mid-point time coordinate at a common max_pos.
        max_pos = max(c.positional_embedding_max_pos[0], c.audio_positional_embedding_max_pos[0])
        av_cross_video = self._precompute_freqs_cis(v_pixel_coords[:, 0:1, :], c.audio_cross_attention_dim, x_dtype,
                                                    [max_pos], c.audio_num_attention_heads,
                                                    use_middle_indices_grid=True, split_mode=split)
        av_cross_audio = self._precompute_freqs_cis(a_latent_coords[:, 0:1, :], c.audio_cross_attention_dim, x_dtype,
                                                    [max_pos], c.audio_num_attention_heads,
                                                    use_middle_indices_grid=True, split_mode=split)
        result = [(v_pe, av_cross_video), (a_pe, av_cross_audio)]
        self._pe_cache_key = cache_key
        self._pe_cache = result
        return result

    def _process_transformer_blocks(self, x, context, attention_mask, timestep, pe,
                                     nag_context=None, nag=None, step_cache=None,
                                     nag_attention_mask=None,
                                     stg_skip_blocks=None, disable_cross_modal=False):
        vx, ax = x
        v_context, a_context = context
        v_context_neg, a_context_neg = nag_context if nag_context is not None else (None, None)
        v_timestep, a_timestep, cross, prompt = timestep
        (av_a_ss, av_v_ss, av_a2v_gate, av_v2a_gate) = cross
        v_prompt, a_prompt = prompt if prompt is not None else (None, None)
        v_pe, av_cross_video = pe[0]
        a_pe, av_cross_audio = pe[1]
        # MultiModalGuider hooks (ported from ltx-core/model/transformer/model.py,
        # Apache-2.0, rev a2c3f24):
        #   stg_skip_blocks: list of block indices whose SELF-ATTENTION is zeroed
        #     (reference: SelfAttentionPerturbation — the self-attn output is
        #     replaced with zeros, effectively skipping only self-attention while
        #     keeping cross-attention and FFN intact).
        #   disable_cross_modal: when True, a2v and v2a cross-attention are
        #     disabled for ALL blocks (reference: modality guidance — the forward
        #     variant that disables audio<->video cross-modal attention so the
        #     guider can measure what each modality contributes).
        stg_set = set(stg_skip_blocks) if stg_skip_blocks else set()
        # FBCache: block-0's output is the change proxy. LTX's audio stream can
        # carry an independently-scheduled timestep (``audio_sigma`` on
        # ``forward``, separate from the video ``sigma``), so audio state can
        # drift even while the video probe stays stable — a video-only probe
        # would let a changed audio track go undetected and reuse a stale
        # audio velocity. The probe is therefore both streams' block-0 output,
        # flattened per-sample and concatenated (``ax`` may be zero-length —
        # no audio track — in which case this degrades to the video-only
        # probe exactly). A skip bypasses blocks 1..N; the caller (forward)
        # then returns the whole cached output structure. ``probe``/
        # ``skipped`` are returned so forward can record on a real compute
        # and short-circuit on a skip.
        probe = None
        for i, block in enumerate(self.transformer_blocks):
            # Per-block a2v / v2a cross-attn gating: disabled when
            # disable_cross_modal=True (modality guidance forward).
            block_a2v = not disable_cross_modal
            block_v2a = not disable_cross_modal
            # STG: skip self-attention at specified blocks. The block's
            # ``run_vx``/``run_ax`` flags gate the ENTIRE video/audio path
            # (self + cross + ff); we need finer control — skip self-attn
            # only. Pass a flag to the block that zeroes self-attn output.
            skip_self_attn = i in stg_set
            vx, ax = block(
                vx, ax, v_context=v_context, a_context=a_context, attention_mask=attention_mask,
                v_timestep=v_timestep, a_timestep=a_timestep, v_pe=v_pe, a_pe=a_pe,
                v_cross_pe=av_cross_video, a_cross_pe=av_cross_audio,
                v_cross_scale_shift_timestep=av_v_ss, a_cross_scale_shift_timestep=av_a_ss,
                v_cross_gate_timestep=av_a2v_gate, a_cross_gate_timestep=av_v2a_gate,
                v_prompt_timestep=v_prompt, a_prompt_timestep=a_prompt,
                v_context_neg=v_context_neg, a_context_neg=a_context_neg, nag=nag,
                nag_attention_mask=nag_attention_mask,
                a2v_cross_attn=block_a2v, v2a_cross_attn=block_v2a,
                skip_self_attn=skip_self_attn,
            )
            if i == 0 and step_cache is not None:
                probe = vx.flatten(1) if ax.numel() == 0 else torch.cat(
                    [vx.flatten(1), ax.flatten(1).to(vx.dtype)], dim=1
                )
                if step_cache.should_skip(probe):
                    return vx, ax, probe, True
        return vx, ax, probe, False

    def _process_output(self, vx, ax, v_embedded, a_embedded, orig_shape, n_extra: int = 0):
        if isinstance(v_embedded, CompressedTimestep):
            v_embedded = v_embedded.expand()
        ss = self.scale_shift_table[None, None].to(vx) + v_embedded[:, :, None]
        vx = self.norm_out(vx) * (1 + ss[:, :, 1]) + ss[:, :, 0]
        vx = self.proj_out(vx)
        # Appended conditioning tokens get the same norm/proj (their velocity is
        # needed for the x0-space conditioning blend) but are split off before the
        # base grid is unpatchified back to 5D.
        extra_velocity = None
        if n_extra:
            extra_velocity = vx[:, -n_extra:]
            vx = vx[:, :-n_extra]
        vx = self.patchifier.unpatchify(
            vx, orig_shape[3], orig_shape[4], orig_shape[2],
            orig_shape[1] // math.prod(self.patchifier.patch_size),
        )
        a_ss = self.audio_scale_shift_table[None, None].to(a_embedded) + a_embedded[:, :, None]
        ax = self.audio_norm_out(ax) * (1 + a_ss[:, :, 1]) + a_ss[:, :, 0]
        ax = self.audio_proj_out(ax)
        ax = self.a_patchifier.unpatchify(ax, channels=self.num_audio_channels, freq=self.audio_frequency_bins)
        out = vx if ax.numel() == 0 else [vx, ax]
        return (out, extra_velocity) if n_extra else out

    def forward(self, x, timestep, context, attention_mask=None, frame_rate=25,
                sigma=None, audio_sigma=None,
                extra_video_tokens=None, extra_video_pixel_coords=None,
                nag_context=None, nag=None, nag_attention_mask=None, **kwargs):
        """LTX-2 AV velocity prediction (RectifiedFlow, true-CFG; the engine sampler
        owns shift 2.37 + guidance).

        ``x``: ``[video_latent (B, C, F, H, W), audio_latent (B, 8, T_audio, 16)]``.
          Audio may be absent / zero-length; then only the video stream runs and the
          return is the video latent alone (else a ``[video, audio]`` list, matching x).
        ``timestep``: scalar-per-batch (or per-token ``[B, S_video]`` — the
          conditioning-mask form ``t * (1 - mask)``) tensor, or a ``(v_timestep,
          a_timestep)`` pair for independent audio/video noise levels.
        ``sigma`` / ``audio_sigma``: per-batch true schedule sigmas (pre-×1000, same
          domain as ``timestep``). REQUIRED whenever the video timestep is per-token
          masked — 2.3's prompt-adaLN and cross-timestep AV modulation must see the
          schedule sigma, not token 0's (possibly zeroed) timestep.
        ``extra_video_tokens`` ``[B, N, in_channels]`` + ``extra_video_pixel_coords``
          ``[B, 3, N, 2]``: appended conditioning tokens (keyframes / IC-LoRA
          references) with caller-built pixel-space coords (temporal axis in pixel
          FRAMES). When given, the return is a 3-tuple ``(video_5d, audio_or_None,
          extra_velocity [B, N, out_channels])``.
        ``context``: DiT caption context ``[B, S, v_inner + a_inner]`` from
          :meth:`apply_text_conditioning` — video/audio halves concatenated on the last
          axis. Returns velocity with x's layout.
        ``nag_context`` / ``nag``: NAG (arXiv:2505.21179, mirrors the Wan arch's
          ``nag_context``/``nag`` contract — see ``src/platform/runtime/native/arch/wan/model.py``).
          ``nag_context`` is the NEGATIVE prompt run through
          :meth:`apply_text_conditioning` the same way as ``context`` — same
          ``[B, S, v_inner + a_inner]`` shape, video/audio halves concatenated on
          the last axis — so it splits into per-stream negative contexts via the
          same ``_prepare_context`` call and reaches both the video (``attn2``)
          and audio (``audio_attn2``) text cross-attention (both are projections
          of the SAME underlying text stream, just through different per-modality
          weights, so both get NAG for v1 — unlike a case where one stream's
          conditioning were a genuinely separate/unrelated source). ``nag`` is
          ``{"scale": float, "tau": float, "alpha": float}``; ``scale <= 1.0`` or
          ``nag_context is None`` is a no-op (byte-identical to pre-NAG code).
        ``nag_attention_mask``: the NEGATIVE prompt's own ``[B, S]`` boolean/int
          key-padding mask (1 = keep), analogous to ``attention_mask`` but for
          ``nag_context``. Never derived from ``attention_mask`` — the positive
          and negative prompts generally have different real-token lengths, so
          reusing the positive mask on the negative context would drop real
          negative tokens or attend negative padding depending on which is
          shorter. ``None`` (the default, and the only value either shipped
          pipe passes today) means the negative cross-attention runs unmasked.
        """
        v_ts, a_ts = timestep if isinstance(timestep, (tuple, list)) and len(timestep) == 2 else (timestep, timestep)
        vx_in = x[0]
        ax_in = x[1] if len(x) > 1 else torch.zeros(
            (vx_in.shape[0], self.num_audio_channels, 0, self.audio_frequency_bins),
            device=vx_in.device, dtype=vx_in.dtype)
        input_dtype = vx_in.dtype
        batch_size = vx_in.shape[0]

        n_extra = extra_video_tokens.shape[1] if extra_video_tokens is not None else 0
        [vx, ax], pixel_coords, orig_shape = self._process_input(
            vx_in, ax_in, extra_video_tokens, extra_video_pixel_coords)
        timestep_list, embedded = self._prepare_timestep(
            v_ts, a_ts, batch_size, input_dtype, orig_shape, sigma=sigma, audio_sigma=audio_sigma)
        context_list, attention_mask = self._prepare_context(context, batch_size, vx, ax, attention_mask)
        nag_context_list = None
        if nag_context is not None:
            nag_context_list, _ = self._prepare_context(nag_context, batch_size, vx, ax)
        nag_attention_mask = self._prepare_attention_mask(nag_attention_mask, input_dtype)
        attention_mask = self._prepare_attention_mask(attention_mask, input_dtype)
        pe = self._prepare_positional_embeddings(pixel_coords, frame_rate, input_dtype)
        # FBCache (video stream gates it; output caching caches the whole variadic
        # return so both streams are byte-identical on a skip). See step_cache.py.
        step_cache = kwargs.get("step_cache")
        # MultiModalGuider hooks (see _process_transformer_blocks for semantics):
        stg_skip_blocks = kwargs.get("stg_skip_blocks")
        disable_cross_modal = kwargs.get("disable_cross_modal", False)
        vx, ax, probe, skipped = self._process_transformer_blocks(
            [vx, ax], context_list, attention_mask, timestep_list, pe,
            nag_context=nag_context_list, nag=nag, step_cache=step_cache,
            nag_attention_mask=nag_attention_mask,
            stg_skip_blocks=stg_skip_blocks, disable_cross_modal=disable_cross_modal)
        if skipped:
            return step_cache.record_skip()
        out = self._process_output(vx, ax, embedded[0], embedded[1], orig_shape, n_extra=n_extra)
        if not n_extra:
            result = out
        else:
            base, extra_velocity = out
            if isinstance(base, list):
                video_5d, audio_out = base
            else:
                video_5d, audio_out = base, None
            result = (video_5d, audio_out, extra_velocity)
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, result)
        return result
