# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/lumina/model.py (Alpha-VLLM NextDiT) @ unknown;
# vendored ~2025 (moved into vendor/gpl/comfyui/z_image/ from
# src/platform/runtime/native/arch/z_image/ as part of the license-relocation
# workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.
# Local modification (BE-97): the native engine's attention-kernel dispatcher
# (src/platform/runtime/native/attention.py) can't be imported here — this
# package must not depend on src. JointAttention.forward calls a module-level
# backend hook instead; src wires it via set_attention_backend() (see
# arch/z_image/model.py, the one importer that constructs ZImageDiT).

"""Z-Image NextDiT building blocks — vendored from ComfyUI's ``comfy/ldm/lumina/model.py``
(Alpha-VLLM NextDiT), adapted to the native ``operations`` seam.

The top-level ``ZImageDiT`` class (``src/platform/runtime/native/arch/z_image/model.py``)
extends ``NativeArchModule`` (PotionUI's own loader contract, not ComfyUI's) and
orchestrates FBCache step-skipping and the learned caption/image pad-token
bookkeeping — none of which touches these blocks, so it stays in src and imports
from here. These classes carry no PotionUI-specific state; they are the sandwich
double-norm blocks with tanh-gated adaLN, the SwiGLU FFN, and the fused-qkv
joint attention with QK-norm + 3-axis RoPE, unchanged from upstream.
"""

from __future__ import annotations

import math
from typing import Callable

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from vendor.gpl.comfyui.flux.math_ops import apply_rope

# Injected by src at import time (see the module docstring). None until then —
# calling _JointAttention.forward before wiring raises rather than silently
# no-oping.
_attention_backend: "Callable[..., Tensor] | None" = None


def set_attention_backend(fn: "Callable[..., Tensor]") -> None:
    """Wire the attention-kernel dispatcher :class:`_JointAttention` calls into.

    ``fn(q, k, v, mask=...) -> Tensor`` — same contract as
    ``src.platform.runtime.native.attention.attention``. Idempotent; safe to
    call more than once (later calls replace the backend).
    """
    global _attention_backend
    _attention_backend = fn


def _timestep_embedding(t: Tensor, dim: int, max_period: int = 10000) -> Tensor:
    """Sinusoidal timestep embedding (ComfyUI mmdit ``TimestepEmbedder``).

    No extra time_factor here — Z-Image applies its ``time_scale`` (1000) to the
    timestep BEFORE this call, so the raw sinusoid must not re-scale it.
    """
    half = dim // 2
    freqs = torch.exp(
        -math.log(max_period) * torch.arange(0, half, dtype=torch.float32, device=t.device) / half
    )
    args = t[:, None].float() * freqs[None]
    emb = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
    if dim % 2:
        emb = torch.cat([emb, torch.zeros_like(emb[:, :1])], dim=-1)
    return emb


class _TimestepEmbedder(nn.Module):
    """``t_embedder``: sinusoid(freq_dim) -> Linear(hidden) -> SiLU -> Linear(out)."""

    def __init__(self, hidden_size: int, output_size: int, operations, freq_dim: int = 256,
                 dtype=None, device=None) -> None:
        super().__init__()
        self.freq_dim = freq_dim
        self.mlp = nn.Sequential(
            operations.Linear(freq_dim, hidden_size, bias=True, dtype=dtype, device=device),
            nn.SiLU(),
            operations.Linear(hidden_size, output_size, bias=True, dtype=dtype, device=device),
        )

    def forward(self, t: Tensor, dtype: torch.dtype) -> Tensor:
        return self.mlp(_timestep_embedding(t, self.freq_dim).to(dtype))


class _JointAttention(nn.Module):
    """Fused-qkv multi-head attention with QK-norm and 3-axis RoPE (no GQA)."""

    def __init__(self, cfg, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.n_heads = cfg.n_heads
        self.n_kv_heads = cfg.n_kv_heads
        self.head_dim = cfg.head_dim
        self.n_rep = self.n_heads // self.n_kv_heads
        self.qkv = operations.Linear(
            cfg.dim, (self.n_heads + 2 * self.n_kv_heads) * self.head_dim,
            bias=False, dtype=dtype, device=device,
        )
        self.out = operations.Linear(self.n_heads * self.head_dim, cfg.dim, bias=False, dtype=dtype, device=device)
        self.q_norm = operations.RMSNorm(self.head_dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
        self.k_norm = operations.RMSNorm(self.head_dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)

    def forward(self, x: Tensor, rope: Tensor) -> Tensor:
        b, s, _ = x.shape
        q, k, v = torch.split(
            self.qkv(x),
            [self.n_heads * self.head_dim, self.n_kv_heads * self.head_dim, self.n_kv_heads * self.head_dim],
            dim=-1,
        )
        q = q.view(b, s, self.n_heads, self.head_dim)
        k = k.view(b, s, self.n_kv_heads, self.head_dim)
        v = v.view(b, s, self.n_kv_heads, self.head_dim)
        q = self.q_norm(q)
        k = self.k_norm(k)
        q, k = apply_rope(q, k, rope)
        if self.n_rep > 1:
            k = k.unsqueeze(3).repeat(1, 1, 1, self.n_rep, 1).flatten(2, 3)
            v = v.unsqueeze(3).repeat(1, 1, 1, self.n_rep, 1).flatten(2, 3)
        if _attention_backend is None:
            raise RuntimeError(
                "z_image layers._JointAttention: no backend wired — call set_attention_backend() first"
            )
        out = _attention_backend(q.movedim(1, 2), k.movedim(1, 2), v.movedim(1, 2), mask=None)  # (B,H,L,D)
        out = out.transpose(1, 2).reshape(b, s, -1)
        return self.out(out)


class _FeedForward(nn.Module):
    """SwiGLU FFN (``w2(silu(w1(x)) * w3(x))``), explicit hidden dim from checkpoint."""

    def __init__(self, cfg, operations, dtype=None, device=None) -> None:
        super().__init__()
        hidden = cfg.intermediate_size
        self.w1 = operations.Linear(cfg.dim, hidden, bias=False, dtype=dtype, device=device)
        self.w2 = operations.Linear(hidden, cfg.dim, bias=False, dtype=dtype, device=device)
        self.w3 = operations.Linear(cfg.dim, hidden, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.w2(F.silu(self.w1(x)) * self.w3(x))


class _JointTransformerBlock(nn.Module):
    """Sandwich double-norm block; adaLN (tanh-gated) when ``modulation``."""

    def __init__(self, cfg, operations, modulation: bool, dtype=None, device=None) -> None:
        super().__init__()
        self.modulation = modulation
        dim = cfg.dim
        self.attention = _JointAttention(cfg, operations, dtype=dtype, device=device)
        self.feed_forward = _FeedForward(cfg, operations, dtype=dtype, device=device)
        self.attention_norm1 = operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
        self.attention_norm2 = operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
        self.ffn_norm1 = operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
        self.ffn_norm2 = operations.RMSNorm(dim, eps=cfg.norm_eps, elementwise_affine=True, dtype=dtype, device=device)
        if modulation:
            # Z-Image adaLN: a bare Linear (no SiLU prefix) over the 256-d t-embed.
            self.adaLN_modulation = nn.Sequential(
                operations.Linear(min(dim, 256), 4 * dim, bias=True, dtype=dtype, device=device),
            )

    def forward(self, x: Tensor, rope: Tensor, adaln_input: Tensor | None) -> Tensor:
        if self.modulation:
            scale_msa, gate_msa, scale_mlp, gate_mlp = self.adaLN_modulation(adaln_input).chunk(4, dim=1)
            x = x + gate_msa.unsqueeze(1).tanh() * self.attention_norm2(
                self.attention(self.attention_norm1(x) * (1 + scale_msa.unsqueeze(1)), rope)
            )
            x = x + gate_mlp.unsqueeze(1).tanh() * self.ffn_norm2(
                self.feed_forward(self.ffn_norm1(x) * (1 + scale_mlp.unsqueeze(1)))
            )
        else:
            x = x + self.attention_norm2(self.attention(self.attention_norm1(x), rope))
            x = x + self.ffn_norm2(self.feed_forward(self.ffn_norm1(x)))
        return x


class _FinalLayer(nn.Module):
    def __init__(self, cfg, operations, dtype=None, device=None) -> None:
        super().__init__()
        dim = cfg.dim
        self.norm_final = operations.LayerNorm(dim, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.linear = operations.Linear(dim, cfg.patch_size * cfg.patch_size * cfg.in_channels, bias=True, dtype=dtype, device=device)
        self.adaLN_modulation = nn.Sequential(
            nn.SiLU(),
            operations.Linear(min(dim, 256), dim, bias=True, dtype=dtype, device=device),
        )

    def forward(self, x: Tensor, c: Tensor) -> Tensor:
        scale = self.adaLN_modulation(c)
        return self.linear(self.norm_final(x) * (1 + scale.unsqueeze(1)))
