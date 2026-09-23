# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/qwen_image21/model.py @ unknown; vendored 2026-09.
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.
# Local modification: attention runs through a module-level backend hook set by src (set_attention_backend) so this package never imports src.

"""Qwen-Image-2.1 single-stream DiT building blocks — the shared timestep/text
projections, the fused SwiGLU feed-forward, the single joint-attention stream,
and the modulated transformer block + AdaLN-continuous final layer.

Unlike Qwen-Image (1.0), 2.1 is single-stream: text and image tokens share
ONE sequence and ONE set of attention projections per block, and modulation
is computed ONCE by a model-level ``nn.Sequential`` shared across every
block — blocks only slice their own scale/gate columns out of it. The
top-level ``QwenImage21DiT`` class
(``src/platform/runtime/native/arch/qwen_image21/model.py``) owns that
modulation MLP, sequence assembly (text + optional reference images + the
target image, target last) and the block-causal mask; this module keeps the
block-level building blocks only.
"""

from __future__ import annotations

from typing import Callable

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from vendor.gpl.comfyui.flux.layers import timestep_embedding
from vendor.gpl.comfyui.flux.math_ops import apply_rope1

_attention_backend: "Callable[..., Tensor] | None" = None


def set_attention_backend(fn: "Callable[..., Tensor]") -> None:
    """Wire the attention-kernel dispatcher :class:`_Attention` calls into.

    ``fn(q, k, v, mask=...) -> Tensor`` — same contract as
    ``src.platform.runtime.native.attention.attention``. Idempotent; safe to
    call more than once (later calls replace the backend).
    """
    global _attention_backend
    _attention_backend = fn


class _ZeroCenteredRMSNorm(nn.Module):
    """RMSNorm whose stored weight is ``scale - 1``; the effective scale
    (``weight + 1``) is computed in fp32 regardless of the module's dtype."""

    def __init__(self, dim: int, eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        self.weight = nn.Parameter(torch.empty(dim, dtype=dtype, device=device))
        self.eps = eps

    def forward(self, x: Tensor) -> Tensor:
        w = self.weight.to(torch.float32) + 1.0
        xf = x.to(torch.float32)
        rrms = torch.rsqrt(xf.pow(2).mean(dim=-1, keepdim=True) + self.eps)
        return (xf * rrms * w).to(x.dtype)


class QwenImage21TextProjection(nn.Module):
    def __init__(self, in_dim: int, hidden_size: int, operations, eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        self.text_norm = _ZeroCenteredRMSNorm(in_dim, eps=eps, dtype=dtype, device=device)
        self.in_layer = operations.Linear(in_dim, hidden_size, bias=False, dtype=dtype, device=device)
        self.out_layer = operations.Linear(hidden_size, hidden_size, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.out_layer(F.gelu(self.in_layer(self.text_norm(x)), approximate="tanh"))


class _TimestepEmbedderNoBias(nn.Module):
    def __init__(self, in_channels: int, time_embed_dim: int, operations, dtype=None, device=None):
        super().__init__()
        self.linear_1 = operations.Linear(in_channels, time_embed_dim, bias=False, dtype=dtype, device=device)
        self.act = nn.SiLU()
        self.linear_2 = operations.Linear(time_embed_dim, time_embed_dim, bias=False, dtype=dtype, device=device)

    def forward(self, sample: Tensor) -> Tensor:
        return self.linear_2(self.act(self.linear_1(sample)))


class QwenImage21TimestepProjEmbeddings(nn.Module):
    def __init__(self, embedding_dim: int, operations, dtype=None, device=None):
        super().__init__()
        self.timestep_embedder = _TimestepEmbedderNoBias(256, embedding_dim, operations, dtype=dtype, device=device)

    def forward(self, timestep: Tensor, dtype: torch.dtype) -> Tensor:
        return self.timestep_embedder(timestep_embedding(timestep.float(), 256).to(dtype))


class _SwiGLUFeedForward(nn.Module):
    """Fused ``[gate; up]`` GEMM — the Comfy-Org single-file layout. A
    diffusers-split checkpoint (separate ``gate_layer``/``proj`` weights) is
    fused into this layout before load, see
    ``arch/qwen_image21/model.py:convert_qwen_image21_state_dict``."""

    def __init__(self, dim: int, hidden_dim: int, operations, dtype=None, device=None):
        super().__init__()
        self.gate_up = operations.Linear(dim, 2 * hidden_dim, bias=False, dtype=dtype, device=device)
        self.out = operations.Linear(hidden_dim, dim, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        gate, up = self.gate_up(x).chunk(2, dim=-1)
        return self.out(F.silu(gate) * up)


class _Attention(nn.Module):
    """Single joint-attention stream over the shared [text | image...] sequence.

    ``q``/``k``/``v`` stay ``(B, H, N, D)`` throughout (never ``(B, N, H, D)``)
    so the RoPE table built by ``EmbedND`` (``model.py``) broadcasts against
    them with no extra transpose, matching the layout ``vendor/gpl/comfyui/
    qwen_image/layers.py`` already uses for the same helper (``apply_rope1``).

    Block-causal reduces to two shapes once split by query row (see
    ``model.py``'s ``_block_causal_mask`` docstring): prefix rows (text +
    reference images) stay causal/same-block over the full key axis, target
    rows (the last, generated image) see the WHOLE sequence unconditionally
    -- ``causal | same_block`` covers every key once the query is in the
    final block. So the target rows never need the dense ``(N, N)`` mask,
    only ``target_key_mask`` (padding, or ``None``): they run through the
    dispatcher unmasked and pick up an accelerated kernel, while prefix rows
    keep the masked SDPA path -- same total attention, a much smaller masked
    call.

    ``prefix_kv``, when given, replaces the whole prefix branch: ``x`` is
    already target rows only (``prefix_len`` is 0 for this call), and the
    cached ``(k_prefix, v_prefix)`` -- captured from an earlier step's
    ``capture_prefix=True`` call, at the same block -- stands in for the
    prefix K/V this call would otherwise recompute. ``capture_prefix``, when
    true, slices the freshly computed prefix K/V (already normed/RoPE'd) out
    of ``k``/``v`` before they're consumed, at no extra compute, and returns
    them alongside the usual output; the two flags are never both set by a
    caller in this codebase.
    """

    def __init__(self, dim: int, heads: int, dim_head: int, operations, eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        self.heads = heads
        inner_dim = heads * dim_head
        self.to_q = operations.Linear(dim, inner_dim, bias=False, dtype=dtype, device=device)
        self.to_k = operations.Linear(dim, inner_dim, bias=False, dtype=dtype, device=device)
        self.to_v = operations.Linear(dim, inner_dim, bias=False, dtype=dtype, device=device)
        self.to_out = nn.ModuleList([operations.Linear(inner_dim, dim, bias=False, dtype=dtype, device=device)])
        self.norm_q = operations.RMSNorm(dim_head, eps=eps, dtype=dtype, device=device)
        self.norm_k = operations.RMSNorm(dim_head, eps=eps, dtype=dtype, device=device)

    def forward(
        self, x: Tensor, pe: Tensor, mask: Tensor | None, prefix_len: int,
        target_key_mask: Tensor | None = None, prefix_kv: tuple[Tensor, Tensor] | None = None,
        capture_prefix: bool = False,
    ) -> Tensor | tuple[Tensor, tuple[Tensor, Tensor] | None]:
        b, n, _ = x.shape

        def split(t: Tensor) -> Tensor:
            return t.view(b, n, self.heads, -1).transpose(1, 2)

        q, k, v = split(self.to_q(x)), split(self.to_k(x)), split(self.to_v(x))
        q, k = self.norm_q(q), self.norm_k(k)
        q = apply_rope1(q, pe)
        k = apply_rope1(k, pe)

        if _attention_backend is None:
            raise RuntimeError(
                "qwen_image21 layers._Attention: no attention backend wired — call set_attention_backend() first"
            )
        captured: tuple[Tensor, Tensor] | None = None
        if prefix_kv is not None:
            k_prefix, v_prefix = prefix_kv
            out = _attention_backend(q, torch.cat([k_prefix, k], dim=2), torch.cat([v_prefix, v], dim=2),
                                      mask=target_key_mask)
        elif prefix_len:
            prefix_mask = mask[..., :prefix_len, :] if mask is not None else None
            prefix_out = _attention_backend(q[:, :, :prefix_len], k, v, mask=prefix_mask)
            target_out = _attention_backend(q[:, :, prefix_len:], k, v, mask=target_key_mask)
            out = torch.cat([prefix_out, target_out], dim=2)  # (B, H, N, D)
            if capture_prefix:
                captured = (k[:, :, :prefix_len].contiguous(), v[:, :, :prefix_len].contiguous())
        else:
            out = _attention_backend(q, k, v, mask=target_key_mask)
        out = out.transpose(1, 2).reshape(b, n, -1)
        result = self.to_out[0](out)
        return (result, captured) if capture_prefix else result


def _modulated_norm(norm, x: Tensor, scale: tuple[Tensor, Tensor], prefix_len: int) -> Tensor:
    """LayerNorm scaled by the shared modulation: rows before ``prefix_len``
    (text + condition images) use the ``t = 0`` scale, the rest (the target
    image) use their own sample's scale."""
    s_prefix, s_target = scale
    out = norm(x)
    if prefix_len:
        return torch.cat([out[:, :prefix_len] * (1 + s_prefix), out[:, prefix_len:] * (1 + s_target)], dim=1)
    return out * (1 + s_target)


def _gated_residual(x: Tensor, y: Tensor, gate: tuple[Tensor, Tensor], prefix_len: int) -> Tensor:
    g_prefix, g_target = gate
    if prefix_len:
        return x + torch.cat([y[:, :prefix_len] * g_prefix, y[:, prefix_len:] * g_target], dim=1)
    return x + y * g_target


class _Block(nn.Module):
    """Single-stream block. Modulation is not learned per block — the parent
    model computes one shared ``modulation`` tensor and every block slices
    its own scale/gate columns out of it (see ``model.py``'s ``_split_rows``)."""

    def __init__(self, dim: int, heads: int, head_dim: int, operations, mlp_ratio: int = 3, eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        self.img_norm1 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.attn = _Attention(dim, heads, head_dim, operations, eps=eps, dtype=dtype, device=device)
        self.img_norm2 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.img_mlp = _SwiGLUFeedForward(dim, dim * mlp_ratio, operations, dtype=dtype, device=device)

    def forward(
        self, x: Tensor, mod, pe: Tensor, mask: Tensor | None, prefix_len: int,
        target_key_mask: Tensor | None = None, prefix_kv: tuple[Tensor, Tensor] | None = None,
        capture_prefix: bool = False,
    ) -> Tensor | tuple[Tensor, tuple[Tensor, Tensor] | None]:
        scale1, gate1, scale2, gate2 = mod
        attn_result = self.attn(
            _modulated_norm(self.img_norm1, x, scale1, prefix_len), pe, mask, prefix_len,
            target_key_mask, prefix_kv, capture_prefix,
        )
        attn_out, captured = attn_result if capture_prefix else (attn_result, None)
        x = _gated_residual(x, attn_out, gate1, prefix_len)
        x = _gated_residual(x, self.img_mlp(_modulated_norm(self.img_norm2, x, scale2, prefix_len)), gate2, prefix_len)
        if x.dtype == torch.float16:
            x = x.clip(-65504, 65504)
        return (x, captured) if capture_prefix else x


class _LastLayer(nn.Module):
    """AdaLN-continuous, scale only (no shift, no learned norm affine)."""

    def __init__(self, dim: int, operations, eps: float = 1e-6, dtype=None, device=None):
        super().__init__()
        self.linear = operations.Linear(dim, dim, bias=False, dtype=dtype, device=device)
        self.norm = operations.LayerNorm(dim, eps=eps, elementwise_affine=False, dtype=dtype, device=device)

    def forward(self, x: Tensor, temb: Tensor) -> Tensor:
        scale = self.linear(F.silu(temb)).unsqueeze(1)
        return self.norm(x) * (1 + scale)
