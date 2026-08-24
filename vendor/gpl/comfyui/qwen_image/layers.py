# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/qwen_image/model.py @ unknown; vendored ~2025 (moved
# into vendor/gpl/comfyui/qwen_image/ from
# src/platform/runtime/native/arch/qwen_image/model.py as part of the
# license-relocation workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.
# Local modification (BE-97): the native engine's attention-kernel dispatcher
# (src/platform/runtime/native/attention.py) can't be imported here — this
# package must not depend on src. _JointAttention.forward calls a module-level
# backend hook instead; src wires it via set_attention_backend() (see
# arch/qwen_image/model.py, the one importer that constructs QwenImageDiT).

"""Qwen-Image MMDiT building blocks — timestep embedding, GELU-tanh feed-forward,
dual-stream joint attention, and the modulated transformer block + final layer.

The top-level ``QwenImageDiT`` class
(``src/platform/runtime/native/arch/qwen_image/model.py``) extends
``NativeArchModule`` (PotionUI's own loader contract) and orchestrates FBCache
step-skipping, the ``ref_latents``/edit-mode concat-and-slice, and the
ControlNet residual seam — none of which touches these blocks except
``_Block``'s optional ``timestep_zero_index`` (BE-111: the 2511 edit
checkpoint's ``index_timestep_zero`` ref method — the model orchestrates
*which* image tokens are reference tokens, this block only knows where the
split falls in the sequence).
"""

from __future__ import annotations

import math
from typing import Callable

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from vendor.gpl.comfyui.flux.math_ops import apply_rope1

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


def _timestep_embedding(t: Tensor, dim: int, scale: float = 1000.0, max_period: int = 10000) -> Tensor:
    """Sinusoidal embedding (diffusers ``get_timestep_embedding``, flip_sin_to_cos,
    downscale_freq_shift=0) — the ComfyUI ``Timesteps`` config for Qwen-Image."""
    half = dim // 2
    exponent = -math.log(max_period) * torch.arange(0, half, dtype=torch.float32, device=t.device) / half
    emb = torch.exp(exponent)
    emb = t[:, None].float() * emb[None, :]
    emb = scale * emb
    emb = torch.cat([torch.sin(emb), torch.cos(emb)], dim=-1)
    # flip_sin_to_cos: [cos, sin]
    emb = torch.cat([emb[:, half:], emb[:, :half]], dim=-1)
    if dim % 2:
        emb = torch.cat([emb, torch.zeros_like(emb[:, :1])], dim=-1)
    return emb


class _TimestepEmbedder(nn.Module):
    def __init__(self, in_channels: int, time_embed_dim: int, operations, dtype=None, device=None):
        super().__init__()
        self.linear_1 = operations.Linear(in_channels, time_embed_dim, bias=True, dtype=dtype, device=device)
        self.act = nn.SiLU()
        self.linear_2 = operations.Linear(time_embed_dim, time_embed_dim, bias=True, dtype=dtype, device=device)

    def forward(self, sample: Tensor) -> Tensor:
        return self.linear_2(self.act(self.linear_1(sample)))


class QwenTimestepProjEmbeddings(nn.Module):
    def __init__(self, embedding_dim: int, use_additional_t_cond: bool, operations, dtype=None, device=None):
        super().__init__()
        self.timestep_embedder = _TimestepEmbedder(256, embedding_dim, operations, dtype=dtype, device=device)
        self.use_additional_t_cond = use_additional_t_cond
        if use_additional_t_cond:
            self.addition_t_embedding = operations.Embedding(2, embedding_dim, device=device, dtype=dtype)

    def forward(self, timestep: Tensor, hidden_states: Tensor, addition_t_cond=None) -> Tensor:
        proj = _timestep_embedding(timestep, 256)
        emb = self.timestep_embedder(proj.to(dtype=hidden_states.dtype))
        if self.use_additional_t_cond:
            if addition_t_cond is None:
                addition_t_cond = torch.zeros((emb.shape[0],), device=emb.device, dtype=torch.long)
            emb = emb + self.addition_t_embedding(addition_t_cond, out_dtype=emb.dtype)
        return emb


class _GELU(nn.Module):
    def __init__(self, dim_in, dim_out, operations, dtype=None, device=None):
        super().__init__()
        self.proj = operations.Linear(dim_in, dim_out, bias=True, dtype=dtype, device=device)

    def forward(self, x):
        return F.gelu(self.proj(x), approximate="tanh")


class _FeedForward(nn.Module):
    def __init__(self, dim, operations, mult=4, dtype=None, device=None):
        super().__init__()
        inner = int(dim * mult)
        self.net = nn.ModuleList([
            _GELU(dim, inner, operations, dtype=dtype, device=device),
            nn.Dropout(0.0),
            operations.Linear(inner, dim, bias=True, dtype=dtype, device=device),
        ])

    def forward(self, x):
        for m in self.net:
            x = m(x)
        return x


class _JointAttention(nn.Module):
    def __init__(self, dim, dim_head, heads, operations, eps=1e-5, dtype=None, device=None):
        super().__init__()
        inner = dim_head * heads
        self.heads = heads
        self.norm_q = operations.RMSNorm(dim_head, eps=eps, elementwise_affine=True, dtype=dtype, device=device)
        self.norm_k = operations.RMSNorm(dim_head, eps=eps, elementwise_affine=True, dtype=dtype, device=device)
        self.norm_added_q = operations.RMSNorm(dim_head, eps=eps, dtype=dtype, device=device)
        self.norm_added_k = operations.RMSNorm(dim_head, eps=eps, dtype=dtype, device=device)
        self.to_q = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.to_k = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.to_v = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.add_q_proj = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.add_k_proj = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.add_v_proj = operations.Linear(dim, inner, bias=True, dtype=dtype, device=device)
        self.to_out = nn.ModuleList([operations.Linear(inner, dim, bias=True, dtype=dtype, device=device), nn.Dropout(0.0)])
        self.to_add_out = operations.Linear(inner, dim, bias=True, dtype=dtype, device=device)

    def forward(self, img: Tensor, txt: Tensor, mask: Tensor | None, rope: Tensor) -> tuple[Tensor, Tensor]:
        b, seq_img, _ = img.shape
        seq_txt = txt.shape[1]

        def split(t):
            return t.view(t.shape[0], t.shape[1], self.heads, -1).transpose(1, 2)

        iq, ik, iv = split(self.to_q(img)), split(self.to_k(img)), split(self.to_v(img))
        tq, tk, tv = split(self.add_q_proj(txt)), split(self.add_k_proj(txt)), split(self.add_v_proj(txt))
        iq, ik = self.norm_q(iq), self.norm_k(ik)
        tq, tk = self.norm_added_q(tq), self.norm_added_k(tk)

        q = torch.cat([tq, iq], dim=2)
        k = torch.cat([tk, ik], dim=2)
        v = torch.cat([tv, iv], dim=2)
        q = apply_rope1(q, rope)
        k = apply_rope1(k, rope)

        attn_mask = None
        if mask is not None:
            attn_mask = torch.zeros((b, 1, 1, seq_txt + seq_img), dtype=q.dtype, device=q.device)
            attn_mask[..., :seq_txt] = mask.to(q.dtype)[:, None, None, :]

        if _attention_backend is None:
            raise RuntimeError(
                "qwen_image layers._JointAttention: no attention backend wired — call set_attention_backend() first"
            )
        out = _attention_backend(q, k, v, mask=attn_mask)         # (B, H, L, D)
        out = out.transpose(1, 2).reshape(b, seq_txt + seq_img, -1)
        txt_out, img_out = out[:, :seq_txt], out[:, seq_txt:]
        img_out = self.to_out[0](img_out)
        txt_out = self.to_add_out(txt_out)
        return img_out, txt_out


class _Block(nn.Module):
    def __init__(self, dim, heads, head_dim, operations, eps=1e-6, dtype=None, device=None):
        super().__init__()
        self.img_mod = nn.Sequential(nn.SiLU(), operations.Linear(dim, 6 * dim, bias=True, dtype=dtype, device=device))
        self.img_norm1 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.img_norm2 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.img_mlp = _FeedForward(dim, operations, dtype=dtype, device=device)
        self.txt_mod = nn.Sequential(nn.SiLU(), operations.Linear(dim, 6 * dim, bias=True, dtype=dtype, device=device))
        self.txt_norm1 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.txt_norm2 = operations.LayerNorm(dim, elementwise_affine=False, eps=eps, dtype=dtype, device=device)
        self.txt_mlp = _FeedForward(dim, operations, dtype=dtype, device=device)
        self.attn = _JointAttention(dim, head_dim, heads, operations, dtype=dtype, device=device)

    @staticmethod
    def _modulate(x, mod, timestep_zero_index=None):
        shift, scale, gate = torch.chunk(mod, 3, dim=-1)
        if timestep_zero_index is None:
            return torch.addcmul(shift.unsqueeze(1), x, 1 + scale.unsqueeze(1)), gate.unsqueeze(1)
        # ``mod`` was computed from a batch-doubled temb (real timestep rows
        # followed by the zeroed-timestep rows, see QwenImageDiT.forward) — the
        # two halves modulate two different SPANS of the same token sequence:
        # shift[:actual_batch] is the real-token modulation (applied to
        # x[:, :timestep_zero_index]), the other half is the zero-timestep
        # modulation (applied to the reference-token span, x[:, timestep_zero_index:]).
        actual_batch = shift.shape[0] // 2
        shift, shift_zero = shift[:actual_batch], shift[actual_batch:]
        scale, scale_zero = scale[:actual_batch], scale[actual_batch:]
        gate, gate_zero = gate[:actual_batch], gate[actual_batch:]
        real = torch.addcmul(shift.unsqueeze(1), x[:, :timestep_zero_index], 1 + scale.unsqueeze(1))
        zero = torch.addcmul(shift_zero.unsqueeze(1), x[:, timestep_zero_index:], 1 + scale_zero.unsqueeze(1))
        return torch.cat([real, zero], dim=1), (gate.unsqueeze(1), gate_zero.unsqueeze(1))

    @staticmethod
    def _apply_gate(x, y, gate, timestep_zero_index=None):
        if timestep_zero_index is None:
            return torch.addcmul(y, gate, x)
        gate_real, gate_zero = gate
        return y + torch.cat([x[:, :timestep_zero_index] * gate_real, x[:, timestep_zero_index:] * gate_zero], dim=1)

    def forward(self, img, txt, mask, temb, rope, timestep_zero_index=None):
        img_mod_params = self.img_mod(temb)
        if timestep_zero_index is not None:
            # Text tokens never include reference tokens — collapse back to
            # the real (non-doubled) batch before deriving the text modulation.
            temb = temb.chunk(2, dim=0)[0]
        txt_mod_params = self.txt_mod(temb)
        img_mod1, img_mod2 = img_mod_params.chunk(2, dim=-1)
        txt_mod1, txt_mod2 = txt_mod_params.chunk(2, dim=-1)

        img_m, img_gate1 = self._modulate(self.img_norm1(img), img_mod1, timestep_zero_index)
        txt_m, txt_gate1 = self._modulate(self.txt_norm1(txt), txt_mod1)
        img_attn, txt_attn = self.attn(img_m, txt_m, mask, rope)

        img = self._apply_gate(img_attn, img, img_gate1, timestep_zero_index)
        txt = torch.addcmul(txt, txt_gate1, txt_attn)

        img_m2, img_gate2 = self._modulate(self.img_norm2(img), img_mod2, timestep_zero_index)
        img = self._apply_gate(self.img_mlp(img_m2), img, img_gate2, timestep_zero_index)
        txt_m2, txt_gate2 = self._modulate(self.txt_norm2(txt), txt_mod2)
        txt = torch.addcmul(txt, txt_gate2, self.txt_mlp(txt_m2))
        return txt, img


class _LastLayer(nn.Module):
    def __init__(self, dim, operations, eps=1e-6, dtype=None, device=None):
        super().__init__()
        self.silu = nn.SiLU()
        self.linear = operations.Linear(dim, dim * 2, bias=True, dtype=dtype, device=device)
        self.norm = operations.LayerNorm(dim, eps=eps, elementwise_affine=False, dtype=dtype, device=device)

    def forward(self, x, cond):
        scale, shift = torch.chunk(self.linear(self.silu(cond)), 2, dim=1)
        return torch.addcmul(shift[:, None, :], self.norm(x), (1 + scale)[:, None, :])
