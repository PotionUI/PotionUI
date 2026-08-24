# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/cosmos/predict2.py (MiniTrainDIT) + comfy/ldm/anima/
# model.py (LLMAdapter) @ unknown; vendored ~2025 (moved into
# vendor/gpl/comfyui/anima/ from src/platform/runtime/native/arch/anima/ as
# part of the license-relocation workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.
#
# Dual ancestry (BE-97 verdict — verified against the actual NVIDIA upstream,
# github.com/nvidia-cosmos/cosmos-predict2, cosmos_predict2/models/
# text2image_dit.py, not just ComfyUI's copy of it):
#
#   MiniTrainDIT-derived, FAITHFUL PORT of NVIDIA's Apache-2.0 original (only
#   context-parallel/distributed-training infra dropped, leading underscores
#   added, torch.nn -> operations seam): _Timesteps, _TimestepEmbedding,
#   _GPT2FeedForward, _PatchEmbed, _FinalLayer, _Block, _adaln. These classes
#   COULD be re-derived directly from NVIDIA's source if the GPL surface here
#   ever needs shrinking.
#
#   MiniTrainDIT-derived, but ComfyUI-ORIGINAL re-expression (not present in
#   NVIDIA's own source to copy from): VideoRopePosition3DEmb,
#   _cosmos_apply_rotary_pos_emb, _Attention. NVIDIA's own RoPE class returns
#   raw, undoubled frequencies and defers the actual rotation to
#   `apply_rotary_pos_emb(..., fused=True)` from the SEPARATE (also NVIDIA,
#   also Apache-2.0, but not part of cosmos-predict2's own published repo)
#   TransformerEngine library — there is nothing in cosmos-predict2 itself to
#   have copied for that operation. ComfyUI's version instead precomputes
#   cos/sin here and packages the rotation as an explicit 2x2-matrix tensor,
#   applied by _cosmos_apply_rotary_pos_emb — a different computational
#   representation of the same RoPE, authored to replace the TE call.
#   _Attention's own attention mechanism (rearrange -> SDPA -> rearrange back)
#   otherwise mirrors NVIDIA's own pure-torch `torch_attention_op` fallback,
#   but it calls the reworked RoPE-apply inline, so the whole class is grouped
#   here rather than split.
#
#   LLMAdapter, ComfyUI-ORIGINAL (no NVIDIA counterpart at all — ComfyUI's own
#   text-fusion head bolted onto the borrowed MiniTrainDIT backbone):
#   _rotate_half, _apply_rope, _AdapterRotaryEmbedding, _AdapterAttention,
#   _AdapterBlock, _LLMAdapter.
#
# Local modification (BE-97): the native engine's attention-kernel dispatcher
# (src/platform/runtime/native/attention.py) can't be imported here — this
# package must not depend on src. _Attention.forward and _AdapterAttention.
# forward both call a module-level backend hook instead; src wires it via
# set_attention_backend() (see arch/anima/model.py, the one importer that
# constructs Anima).

"""Anima building blocks: MiniTrainDIT backbone layers + the in-model
LLMAdapter text-fusion head. See the header above for per-class provenance.

The top-level ``Anima`` class (``src/platform/runtime/native/arch/anima/model.py``)
extends ``NativeArchModule`` (PotionUI's own loader contract, not ComfyUI's or
NVIDIA's) and orchestrates FBCache step-skipping + the text-fusion call, so it
stays in src and imports from here.
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Tuple

import torch
import torch.nn as nn
from einops import rearrange, repeat
from einops.layers.torch import Rearrange

Tensor = torch.Tensor

# Injected by src at import time (see the module docstring). None until then —
# calling _Attention.forward/_AdapterAttention.forward before wiring raises
# rather than silently no-oping.
_attention_backend: "Callable[..., Tensor] | None" = None


def set_attention_backend(fn: "Callable[..., Tensor]") -> None:
    """Wire the attention-kernel dispatcher :class:`_Attention` and
    :class:`_AdapterAttention` call into.

    ``fn(q, k, v) -> Tensor`` — same contract as
    ``src.platform.runtime.native.attention.attention``. Idempotent; safe to
    call more than once (later calls replace the backend).
    """
    global _attention_backend
    _attention_backend = fn


def _call_attention_backend(q: Tensor, k: Tensor, v: Tensor) -> Tensor:
    if _attention_backend is None:
        raise RuntimeError(
            "anima layers: no attention backend wired — call set_attention_backend() first"
        )
    return _attention_backend(q, k, v)


# ---------------------------------------------------------------------------
# MiniTrainDIT 3D RoPE (cosmos/position_embedding.py — ComfyUI-original
# re-expression, see the module header).
# The two range buffers are non-persistent (never in the checkpoint), so
# meta-device construction leaves them garbage: post_load recomputes them.
# ---------------------------------------------------------------------------
def _cosmos_apply_rotary_pos_emb(t: Tensor, freqs: Tensor) -> Tensor:
    t_ = t.reshape(*t.shape[:-1], 2, -1).movedim(-2, -1).unsqueeze(-2).float()
    t_out = freqs[..., 0] * t_[..., 0] + freqs[..., 1] * t_[..., 1]
    t_out = t_out.movedim(-1, -2).reshape(*t.shape).type_as(t)
    return t_out


class VideoRopePosition3DEmb(nn.Module):
    """3D (T/H/W) rotary positional embedding for the DiT self-attention."""

    def __init__(
        self,
        *,
        head_dim: int,
        len_h: int,
        len_w: int,
        len_t: int,
        base_fps: int = 24,
        h_extrapolation_ratio: float = 1.0,
        w_extrapolation_ratio: float = 1.0,
        t_extrapolation_ratio: float = 1.0,
        enable_fps_modulation: bool = True,
    ) -> None:
        super().__init__()
        self.base_fps = base_fps
        self.max_h = len_h
        self.max_w = len_w
        self.head_dim = head_dim
        self.enable_fps_modulation = enable_fps_modulation

        dim_h = head_dim // 6 * 2
        dim_t = head_dim - 2 * dim_h
        self.dim_h = dim_h
        self.dim_t = dim_t
        self.register_buffer("dim_spatial_range", self._spatial_range(), persistent=False)
        self.register_buffer("dim_temporal_range", self._temporal_range(), persistent=False)

        self.h_ntk_factor = h_extrapolation_ratio ** (dim_h / (dim_h - 2))
        self.w_ntk_factor = w_extrapolation_ratio ** (dim_h / (dim_h - 2))
        self.t_ntk_factor = t_extrapolation_ratio ** (dim_t / (dim_t - 2))

    def _spatial_range(self, device=None) -> Tensor:
        return torch.arange(0, self.dim_h, 2, device=device)[: (self.dim_h // 2)].float() / self.dim_h

    def _temporal_range(self, device=None) -> Tensor:
        return torch.arange(0, self.dim_t, 2, device=device)[: (self.dim_t // 2)].float() / self.dim_t

    def recompute_buffers(self, device=None) -> None:
        """Rebuild the two non-persistent range buffers (post_load hook)."""
        self.dim_spatial_range = self._spatial_range(device)
        self.dim_temporal_range = self._temporal_range(device)

    def forward(self, x_B_T_H_W_C: Tensor, fps: Optional[Tensor] = None, device=None) -> Tensor:
        B, T, H, W, _ = x_B_T_H_W_C.shape
        h_ntk, w_ntk, t_ntk = self.h_ntk_factor, self.w_ntk_factor, self.t_ntk_factor
        h_theta, w_theta, t_theta = 10000.0 * h_ntk, 10000.0 * w_ntk, 10000.0 * t_ntk

        h_spatial_freqs = 1.0 / (h_theta ** self.dim_spatial_range.to(device=device))
        w_spatial_freqs = 1.0 / (w_theta ** self.dim_spatial_range.to(device=device))
        temporal_freqs = 1.0 / (t_theta ** self.dim_temporal_range.to(device=device))

        seq = torch.arange(max(H, W, T), dtype=torch.float, device=device)
        half_emb_h = torch.outer(seq[:H].to(device=device), h_spatial_freqs)
        half_emb_w = torch.outer(seq[:W].to(device=device), w_spatial_freqs)
        if fps is None or self.enable_fps_modulation is False:
            half_emb_t = torch.outer(seq[:T].to(device=device), temporal_freqs)
        else:
            half_emb_t = torch.outer(seq[:T].to(device=device) / fps * self.base_fps, temporal_freqs)

        half_emb_h = torch.stack([torch.cos(half_emb_h), -torch.sin(half_emb_h), torch.sin(half_emb_h), torch.cos(half_emb_h)], dim=-1)
        half_emb_w = torch.stack([torch.cos(half_emb_w), -torch.sin(half_emb_w), torch.sin(half_emb_w), torch.cos(half_emb_w)], dim=-1)
        half_emb_t = torch.stack([torch.cos(half_emb_t), -torch.sin(half_emb_t), torch.sin(half_emb_t), torch.cos(half_emb_t)], dim=-1)

        em_T_H_W_D = torch.cat(
            [
                repeat(half_emb_t, "t d x -> t h w d x", h=H, w=W),
                repeat(half_emb_h, "h d x -> t h w d x", t=T, w=W),
                repeat(half_emb_w, "w d x -> t h w d x", t=T, h=H),
            ],
            dim=-2,
        )
        return rearrange(em_T_H_W_D, "t h w d (i j) -> (t h w) d i j", i=2, j=2).float()


# ---------------------------------------------------------------------------
# MiniTrainDIT layers — faithful port (mechanical trims + ops seam only), see
# the module header.
# ---------------------------------------------------------------------------
class _Timesteps(nn.Module):
    def __init__(self, num_channels: int) -> None:
        super().__init__()
        self.num_channels = num_channels

    def forward(self, timesteps_B_T: Tensor) -> Tensor:
        assert timesteps_B_T.ndim == 2
        timesteps = timesteps_B_T.flatten().float()
        half_dim = self.num_channels // 2
        exponent = -math.log(10000) * torch.arange(half_dim, dtype=torch.float32, device=timesteps.device)
        exponent = exponent / (half_dim - 0.0)
        emb = torch.exp(exponent)
        emb = timesteps[:, None].float() * emb[None, :]
        emb = torch.cat([torch.cos(emb), torch.sin(emb)], dim=-1)
        return rearrange(emb, "(b t) d -> b t d", b=timesteps_B_T.shape[0], t=timesteps_B_T.shape[1])


class _TimestepEmbedding(nn.Module):
    def __init__(self, in_features: int, out_features: int, use_adaln_lora: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.linear_1 = operations.Linear(in_features, out_features, bias=not use_adaln_lora, device=device, dtype=dtype)
        self.activation = nn.SiLU()
        self.use_adaln_lora = use_adaln_lora
        if use_adaln_lora:
            self.linear_2 = operations.Linear(out_features, 3 * out_features, bias=False, device=device, dtype=dtype)
        else:
            self.linear_2 = operations.Linear(out_features, out_features, bias=False, device=device, dtype=dtype)

    def forward(self, sample: Tensor) -> Tuple[Tensor, Optional[Tensor]]:
        emb = self.linear_2(self.activation(self.linear_1(sample)))
        if self.use_adaln_lora:
            return sample, emb
        return emb, None


class _PatchEmbed(nn.Module):
    def __init__(self, spatial_patch_size: int, temporal_patch_size: int, in_channels: int,
                 out_channels: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.spatial_patch_size = spatial_patch_size
        self.temporal_patch_size = temporal_patch_size
        self.proj = nn.Sequential(
            Rearrange(
                "b c (t r) (h m) (w n) -> b t h w (c r m n)",
                r=temporal_patch_size, m=spatial_patch_size, n=spatial_patch_size,
            ),
            operations.Linear(
                in_channels * spatial_patch_size * spatial_patch_size * temporal_patch_size,
                out_channels, bias=False, device=device, dtype=dtype,
            ),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.proj(x)


class _Attention(nn.Module):
    """MiniTrainDIT attention (self- or cross-, RMS-normed q/k, RoPE on self-attn)."""

    def __init__(self, query_dim: int, context_dim: Optional[int], n_heads: int, head_dim: int,
                 operations, device=None, dtype=None) -> None:
        super().__init__()
        self.is_selfattn = context_dim is None
        context_dim = query_dim if context_dim is None else context_dim
        inner_dim = head_dim * n_heads
        self.n_heads = n_heads
        self.head_dim = head_dim
        self.q_proj = operations.Linear(query_dim, inner_dim, bias=False, device=device, dtype=dtype)
        self.q_norm = operations.RMSNorm(head_dim, eps=1e-6, device=device, dtype=dtype)
        self.k_proj = operations.Linear(context_dim, inner_dim, bias=False, device=device, dtype=dtype)
        self.k_norm = operations.RMSNorm(head_dim, eps=1e-6, device=device, dtype=dtype)
        self.v_proj = operations.Linear(context_dim, inner_dim, bias=False, device=device, dtype=dtype)
        self.output_proj = operations.Linear(inner_dim, query_dim, bias=False, device=device, dtype=dtype)

    def forward(self, x: Tensor, context: Optional[Tensor] = None, rope_emb: Optional[Tensor] = None) -> Tensor:
        context = x if context is None else context
        q = rearrange(self.q_proj(x), "b s (h d) -> b s h d", h=self.n_heads, d=self.head_dim)
        k = rearrange(self.k_proj(context), "b s (h d) -> b s h d", h=self.n_heads, d=self.head_dim)
        v = rearrange(self.v_proj(context), "b s (h d) -> b s h d", h=self.n_heads, d=self.head_dim)
        q = self.q_norm(q)
        k = self.k_norm(k)
        if self.is_selfattn and rope_emb is not None:
            q = _cosmos_apply_rotary_pos_emb(q, rope_emb)
            k = _cosmos_apply_rotary_pos_emb(k, rope_emb)
        # (B, S, H, D) -> (B, H, S, D) for the attention dispatcher -> back.
        out = _call_attention_backend(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2))
        out = out.transpose(1, 2).reshape(*x.shape[:-1], -1)
        return self.output_proj(out)


class _GPT2FeedForward(nn.Module):
    def __init__(self, d_model: int, d_ff: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.activation = nn.GELU()
        self.layer1 = operations.Linear(d_model, d_ff, bias=False, device=device, dtype=dtype)
        self.layer2 = operations.Linear(d_ff, d_model, bias=False, device=device, dtype=dtype)

    def forward(self, x: Tensor) -> Tensor:
        return self.layer2(self.activation(self.layer1(x)))


def _adaln(x_dim: int, adaln_lora_dim: int, use_adaln_lora: bool, out_mult: int, operations, device, dtype) -> nn.Sequential:
    if use_adaln_lora:
        return nn.Sequential(
            nn.SiLU(),
            operations.Linear(x_dim, adaln_lora_dim, bias=False, device=device, dtype=dtype),
            operations.Linear(adaln_lora_dim, out_mult * x_dim, bias=False, device=device, dtype=dtype),
        )
    return nn.Sequential(nn.SiLU(), operations.Linear(x_dim, out_mult * x_dim, bias=False, device=device, dtype=dtype))


class _Block(nn.Module):
    def __init__(self, x_dim: int, context_dim: int, num_heads: int, mlp_ratio: float,
                 use_adaln_lora: bool, adaln_lora_dim: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.use_adaln_lora = use_adaln_lora
        self.layer_norm_self_attn = nn.LayerNorm(x_dim, elementwise_affine=False, eps=1e-6)
        self.self_attn = _Attention(x_dim, None, num_heads, x_dim // num_heads, operations, device=device, dtype=dtype)
        self.layer_norm_cross_attn = nn.LayerNorm(x_dim, elementwise_affine=False, eps=1e-6)
        self.cross_attn = _Attention(x_dim, context_dim, num_heads, x_dim // num_heads, operations, device=device, dtype=dtype)
        self.layer_norm_mlp = nn.LayerNorm(x_dim, elementwise_affine=False, eps=1e-6)
        self.mlp = _GPT2FeedForward(x_dim, int(x_dim * mlp_ratio), operations, device=device, dtype=dtype)
        self.adaln_modulation_self_attn = _adaln(x_dim, adaln_lora_dim, use_adaln_lora, 3, operations, device, dtype)
        self.adaln_modulation_cross_attn = _adaln(x_dim, adaln_lora_dim, use_adaln_lora, 3, operations, device, dtype)
        self.adaln_modulation_mlp = _adaln(x_dim, adaln_lora_dim, use_adaln_lora, 3, operations, device, dtype)

    @staticmethod
    def _mod(seq: nn.Sequential, emb: Tensor, adaln_lora: Optional[Tensor]) -> Tuple[Tensor, Tensor, Tensor]:
        out = seq(emb)
        if adaln_lora is not None:
            out = out + adaln_lora
        return out.chunk(3, dim=-1)

    def forward(self, x_B_T_H_W_D: Tensor, emb_B_T_D: Tensor, crossattn_emb: Tensor,
                rope_emb: Optional[Tensor], adaln_lora_B_T_3D: Optional[Tensor]) -> Tensor:
        B, T, H, W, D = x_B_T_H_W_D.shape

        def to_1_1(v):
            return rearrange(v, "b t d -> b t 1 1 d")

        def modulate(x, norm, scale, shift):
            return norm(x) * (1 + scale) + shift

        s_sa, sc_sa, g_sa = self._mod(self.adaln_modulation_self_attn, emb_B_T_D, adaln_lora_B_T_3D)
        s_ca, sc_ca, g_ca = self._mod(self.adaln_modulation_cross_attn, emb_B_T_D, adaln_lora_B_T_3D)
        s_mlp, sc_mlp, g_mlp = self._mod(self.adaln_modulation_mlp, emb_B_T_D, adaln_lora_B_T_3D)
        s_sa, sc_sa, g_sa = to_1_1(s_sa), to_1_1(sc_sa), to_1_1(g_sa)
        s_ca, sc_ca, g_ca = to_1_1(s_ca), to_1_1(sc_ca), to_1_1(g_ca)
        s_mlp, sc_mlp, g_mlp = to_1_1(s_mlp), to_1_1(sc_mlp), to_1_1(g_mlp)

        normed = modulate(x_B_T_H_W_D, self.layer_norm_self_attn, sc_sa, s_sa)
        attn = self.self_attn(rearrange(normed, "b t h w d -> b (t h w) d"), None, rope_emb=rope_emb)
        attn = rearrange(attn, "b (t h w) d -> b t h w d", t=T, h=H, w=W)
        x_B_T_H_W_D = x_B_T_H_W_D + g_sa * attn

        normed = modulate(x_B_T_H_W_D, self.layer_norm_cross_attn, sc_ca, s_ca)
        attn = self.cross_attn(rearrange(normed, "b t h w d -> b (t h w) d"), crossattn_emb, rope_emb=None)
        attn = rearrange(attn, "b (t h w) d -> b t h w d", t=T, h=H, w=W)
        x_B_T_H_W_D = attn * g_ca + x_B_T_H_W_D

        normed = modulate(x_B_T_H_W_D, self.layer_norm_mlp, sc_mlp, s_mlp)
        x_B_T_H_W_D = x_B_T_H_W_D + g_mlp * self.mlp(normed)
        return x_B_T_H_W_D


class _FinalLayer(nn.Module):
    def __init__(self, hidden_size: int, spatial_patch_size: int, temporal_patch_size: int,
                 out_channels: int, use_adaln_lora: bool, adaln_lora_dim: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.layer_norm = nn.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6)
        self.linear = operations.Linear(
            hidden_size, spatial_patch_size * spatial_patch_size * temporal_patch_size * out_channels,
            bias=False, device=device, dtype=dtype,
        )
        self.hidden_size = hidden_size
        self.use_adaln_lora = use_adaln_lora
        self.adaln_modulation = _adaln(hidden_size, adaln_lora_dim, use_adaln_lora, 2, operations, device, dtype)

    def forward(self, x_B_T_H_W_D: Tensor, emb_B_T_D: Tensor, adaln_lora_B_T_3D: Optional[Tensor]) -> Tensor:
        mod = self.adaln_modulation(emb_B_T_D)
        if self.use_adaln_lora:
            mod = mod + adaln_lora_B_T_3D[:, :, : 2 * self.hidden_size]
        shift, scale = mod.chunk(2, dim=-1)
        shift = rearrange(shift, "b t d -> b t 1 1 d")
        scale = rearrange(scale, "b t d -> b t 1 1 d")
        x = self.layer_norm(x_B_T_H_W_D) * (1 + scale) + shift
        return self.linear(x)


# ---------------------------------------------------------------------------
# LLMAdapter (anima/model.py — ComfyUI-original, no NVIDIA counterpart, see
# the module header). Its RotaryEmbedding.inv_freq is a non-persistent
# buffer -> recomputed in post_load.
# ---------------------------------------------------------------------------
def _rotate_half(x: Tensor) -> Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)


def _apply_rope(x: Tensor, cos: Tensor, sin: Tensor, unsqueeze_dim: int = 1) -> Tensor:
    cos = cos.unsqueeze(unsqueeze_dim)
    sin = sin.unsqueeze(unsqueeze_dim)
    return (x * cos) + (_rotate_half(x) * sin)


class _AdapterRotaryEmbedding(nn.Module):
    def __init__(self, head_dim: int) -> None:
        super().__init__()
        self.head_dim = head_dim
        self.rope_theta = 10000
        self.register_buffer("inv_freq", self._inv_freq(), persistent=False)

    def _inv_freq(self, device=None) -> Tensor:
        rng = torch.arange(0, self.head_dim, 2, dtype=torch.int64, device=device).float() / self.head_dim
        return 1.0 / (self.rope_theta ** rng)

    def recompute_inv_freq(self, device=None) -> None:
        self.inv_freq = self._inv_freq(device)

    @torch.no_grad()
    def forward(self, x: Tensor, position_ids: Tensor) -> Tuple[Tensor, Tensor]:
        inv_freq = self.inv_freq[None, :, None].float().expand(position_ids.shape[0], -1, 1).to(x.device)
        pos = position_ids[:, None, :].float()
        freqs = (inv_freq @ pos).transpose(1, 2)
        emb = torch.cat((freqs, freqs), dim=-1)
        return emb.cos().to(dtype=x.dtype), emb.sin().to(dtype=x.dtype)


class _AdapterAttention(nn.Module):
    def __init__(self, query_dim: int, context_dim: int, n_heads: int, head_dim: int,
                 operations, device=None, dtype=None) -> None:
        super().__init__()
        inner = head_dim * n_heads
        self.n_heads = n_heads
        self.head_dim = head_dim
        self.q_proj = operations.Linear(query_dim, inner, bias=False, device=device, dtype=dtype)
        self.q_norm = operations.RMSNorm(head_dim, eps=1e-6, device=device, dtype=dtype)
        self.k_proj = operations.Linear(context_dim, inner, bias=False, device=device, dtype=dtype)
        self.k_norm = operations.RMSNorm(head_dim, eps=1e-6, device=device, dtype=dtype)
        self.v_proj = operations.Linear(context_dim, inner, bias=False, device=device, dtype=dtype)
        self.o_proj = operations.Linear(inner, query_dim, bias=False, device=device, dtype=dtype)

    def forward(self, x: Tensor, context: Optional[Tensor], pe_q, pe_k) -> Tensor:
        context = x if context is None else context
        q = self.q_norm(self.q_proj(x).view(*x.shape[:-1], self.n_heads, self.head_dim)).transpose(1, 2)
        k = self.k_norm(self.k_proj(context).view(*context.shape[:-1], self.n_heads, self.head_dim)).transpose(1, 2)
        v = self.v_proj(context).view(*context.shape[:-1], self.n_heads, self.head_dim).transpose(1, 2)
        if pe_q is not None:
            q = _apply_rope(q, *pe_q)
            k = _apply_rope(k, *pe_k)
        out = _call_attention_backend(q, k, v)
        out = out.transpose(1, 2).reshape(*x.shape[:-1], -1)
        return self.o_proj(out)


class _AdapterBlock(nn.Module):
    def __init__(self, source_dim: int, model_dim: int, num_heads: int, mlp_ratio: float,
                 use_self_attn: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.use_self_attn = use_self_attn
        if use_self_attn:
            self.norm_self_attn = operations.RMSNorm(model_dim, eps=1e-6, device=device, dtype=dtype)
            self.self_attn = _AdapterAttention(model_dim, model_dim, num_heads, model_dim // num_heads, operations, device=device, dtype=dtype)
        self.norm_cross_attn = operations.RMSNorm(model_dim, eps=1e-6, device=device, dtype=dtype)
        self.cross_attn = _AdapterAttention(model_dim, source_dim, num_heads, model_dim // num_heads, operations, device=device, dtype=dtype)
        self.norm_mlp = operations.RMSNorm(model_dim, eps=1e-6, device=device, dtype=dtype)
        self.mlp = nn.Sequential(
            operations.Linear(model_dim, int(model_dim * mlp_ratio), device=device, dtype=dtype),
            nn.GELU(),
            operations.Linear(int(model_dim * mlp_ratio), model_dim, device=device, dtype=dtype),
        )

    def forward(self, x: Tensor, context: Tensor, pe_target, pe_context) -> Tensor:
        if self.use_self_attn:
            x = x + self.self_attn(self.norm_self_attn(x), None, pe_target, pe_target)
        x = x + self.cross_attn(self.norm_cross_attn(x), context, pe_target, pe_context)
        x = x + self.mlp(self.norm_mlp(x))
        return x


class _LLMAdapter(nn.Module):
    """Fuse Qwen3 hidden (source) + T5 token ids (target) -> DiT cross-attn context."""

    def __init__(self, source_dim: int, target_dim: int, model_dim: int, num_layers: int,
                 num_heads: int, vocab_size: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.embed = operations.Embedding(vocab_size, target_dim, device=device, dtype=dtype)
        self.in_proj = nn.Identity() if model_dim == target_dim else operations.Linear(target_dim, model_dim, device=device, dtype=dtype)
        self.rotary_emb = _AdapterRotaryEmbedding(model_dim // num_heads)
        self.blocks = nn.ModuleList([
            _AdapterBlock(source_dim, model_dim, num_heads, 4.0, True, operations, device=device, dtype=dtype)
            for _ in range(num_layers)
        ])
        self.out_proj = operations.Linear(model_dim, target_dim, device=device, dtype=dtype)
        self.norm = operations.RMSNorm(target_dim, eps=1e-6, device=device, dtype=dtype)

    def forward(self, source_hidden_states: Tensor, target_input_ids: Tensor) -> Tensor:
        x = self.in_proj(self.embed(target_input_ids))
        context = source_hidden_states
        pos_t = torch.arange(x.shape[1], device=x.device).unsqueeze(0)
        pos_c = torch.arange(context.shape[1], device=x.device).unsqueeze(0)
        pe_target = self.rotary_emb(x, pos_t)
        pe_context = self.rotary_emb(x, pos_c)
        for block in self.blocks:
            x = block(x, context, pe_target, pe_context)
        return self.norm(self.out_proj(x))
