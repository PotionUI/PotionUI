# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/flux/layers.py @ unknown; vendored ~2025 (moved into
# vendor/gpl/comfyui/flux/ from src/platform/runtime/native/arch/flux/ as part
# of the license-relocation workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.

"""Flux DiT layer modules — vendored from ComfyUI ``comfy/ldm/flux/layers.py``.

Faithful port, with the runtime-plumbing stripped so the blocks are pure PyTorch
against the ``operations`` namespace:

  * ``transformer_options`` / ``patches`` / ``patches_replace`` threading removed;
  * the ``optimized_attention`` dispatch replaced by ``math_ops.attention`` (sdpa);
  * ``modulation_dims`` (regional prompting) dropped — always ``None`` here;
  * a minimal ``attn_mask`` seam is kept (Flux2 pads context to 512 and masks).

Layer/parameter names are unchanged so checkpoint keys map 1:1. ``RMSNorm`` keeps
ComfyUI's ``.scale`` parameter name (not torch's ``.weight``) because the Flux
QK-norm checkpoint keys are ``...norm.{query,key}_norm.scale``. Norm scales are
raw ``nn.Parameter``s (not built through ``operations``), matching ComfyUI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import torch
from torch import Tensor, nn

from .math_ops import attention


class EmbedND(nn.Module):
    def __init__(self, dim: int, theta: int, axes_dim: list[int]):
        super().__init__()
        self.dim = dim
        self.theta = theta
        self.axes_dim = axes_dim

    def forward(self, ids: Tensor) -> Tensor:
        from .math_ops import rope

        n_axes = ids.shape[-1]
        emb = torch.cat(
            [rope(ids[..., i], self.axes_dim[i], self.theta) for i in range(n_axes)],
            dim=-3,
        )
        return emb.unsqueeze(1)


def timestep_embedding(t: Tensor, dim, max_period=10000, time_factor: float = 1000.0) -> Tensor:
    """Sinusoidal timestep embeddings (fractional t supported)."""
    t = time_factor * t
    half = dim // 2
    freqs = torch.exp(
        -math.log(max_period) * torch.arange(start=0, end=half, dtype=torch.float32, device=t.device) / half
    )
    args = t[:, None].float() * freqs[None]
    embedding = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
    if dim % 2:
        embedding = torch.cat([embedding, torch.zeros_like(embedding[:, :1])], dim=-1)
    if torch.is_floating_point(t):
        embedding = embedding.to(t)
    return embedding


class MLPEmbedder(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, bias=True, dtype=None, device=None, operations=None):
        super().__init__()
        self.in_layer = operations.Linear(in_dim, hidden_dim, bias=bias, dtype=dtype, device=device)
        self.silu = nn.SiLU()
        self.out_layer = operations.Linear(hidden_dim, hidden_dim, bias=bias, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.out_layer(self.silu(self.in_layer(x)))


class RMSNorm(nn.Module):
    """RMSNorm with ComfyUI's ``.scale`` parameter name (matches checkpoint keys)."""

    def __init__(self, dim: int, dtype=None, device=None, operations=None):
        super().__init__()
        self.scale = nn.Parameter(torch.empty((dim), dtype=dtype, device=device))

    def forward(self, x: Tensor) -> Tensor:
        weight = self.scale.to(dtype=x.dtype, device=x.device)
        return torch.nn.functional.rms_norm(x, weight.shape, weight=weight, eps=1e-6)


class QKNorm(nn.Module):
    def __init__(self, dim: int, dtype=None, device=None, operations=None):
        super().__init__()
        self.query_norm = RMSNorm(dim, dtype=dtype, device=device, operations=operations)
        self.key_norm = RMSNorm(dim, dtype=dtype, device=device, operations=operations)

    def forward(self, q: Tensor, k: Tensor, v: Tensor) -> tuple[Tensor, Tensor]:
        q = self.query_norm(q)
        k = self.key_norm(k)
        return q.to(v), k.to(v)


class SelfAttention(nn.Module):
    def __init__(self, dim: int, num_heads: int = 8, qkv_bias: bool = False, proj_bias: bool = True,
                 dtype=None, device=None, operations=None):
        super().__init__()
        self.num_heads = num_heads
        head_dim = dim // num_heads
        self.qkv = operations.Linear(dim, dim * 3, bias=qkv_bias, dtype=dtype, device=device)
        self.norm = QKNorm(head_dim, dtype=dtype, device=device, operations=operations)
        self.proj = operations.Linear(dim, dim, bias=proj_bias, dtype=dtype, device=device)


@dataclass
class ModulationOut:
    shift: Tensor
    scale: Tensor
    gate: Tensor


class Modulation(nn.Module):
    def __init__(self, dim: int, double: bool, bias=True, dtype=None, device=None, operations=None):
        super().__init__()
        self.is_double = double
        self.multiplier = 6 if double else 3
        self.lin = operations.Linear(dim, self.multiplier * dim, bias=bias, dtype=dtype, device=device)

    def forward(self, vec: Tensor) -> tuple[ModulationOut, ModulationOut | None]:
        if vec.ndim == 2:
            vec = vec[:, None, :]
        out = self.lin(nn.functional.silu(vec)).chunk(self.multiplier, dim=-1)
        return (
            ModulationOut(*out[:3]),
            ModulationOut(*out[3:]) if self.is_double else None,
        )


def apply_mod(tensor: Tensor, m_mult: Tensor, m_add: Tensor | None = None) -> Tensor:
    if m_add is not None:
        return torch.addcmul(m_add, tensor, m_mult)
    return tensor * m_mult


class SiLUActivation(nn.Module):
    def __init__(self):
        super().__init__()
        self.gate_fn = nn.SiLU()

    def forward(self, x: Tensor) -> Tensor:
        x1, x2 = x.chunk(2, dim=-1)
        return self.gate_fn(x1) * x2


def build_mlp(hidden_size, mlp_hidden_dim, mlp_silu_act=False, dtype=None, device=None, operations=None) -> nn.Module:
    if mlp_silu_act:
        return nn.Sequential(
            operations.Linear(hidden_size, mlp_hidden_dim * 2, bias=False, dtype=dtype, device=device),
            SiLUActivation(),
            operations.Linear(mlp_hidden_dim, hidden_size, bias=False, dtype=dtype, device=device),
        )
    return nn.Sequential(
        operations.Linear(hidden_size, mlp_hidden_dim, bias=True, dtype=dtype, device=device),
        nn.GELU(approximate="tanh"),
        operations.Linear(mlp_hidden_dim, hidden_size, bias=True, dtype=dtype, device=device),
    )


class DoubleStreamBlock(nn.Module):
    def __init__(self, hidden_size: int, num_heads: int, mlp_ratio: float, qkv_bias: bool = False,
                 modulation=True, mlp_silu_act=False, proj_bias=True,
                 dtype=None, device=None, operations=None):
        super().__init__()
        mlp_hidden_dim = int(hidden_size * mlp_ratio)
        self.num_heads = num_heads
        self.hidden_size = hidden_size
        self.modulation = modulation

        if self.modulation:
            self.img_mod = Modulation(hidden_size, double=True, dtype=dtype, device=device, operations=operations)
        self.img_norm1 = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.img_attn = SelfAttention(dim=hidden_size, num_heads=num_heads, qkv_bias=qkv_bias, proj_bias=proj_bias,
                                      dtype=dtype, device=device, operations=operations)
        self.img_norm2 = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.img_mlp = build_mlp(hidden_size, mlp_hidden_dim, mlp_silu_act=mlp_silu_act,
                                 dtype=dtype, device=device, operations=operations)

        if self.modulation:
            self.txt_mod = Modulation(hidden_size, double=True, dtype=dtype, device=device, operations=operations)
        self.txt_norm1 = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.txt_attn = SelfAttention(dim=hidden_size, num_heads=num_heads, qkv_bias=qkv_bias, proj_bias=proj_bias,
                                      dtype=dtype, device=device, operations=operations)
        self.txt_norm2 = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.txt_mlp = build_mlp(hidden_size, mlp_hidden_dim, mlp_silu_act=mlp_silu_act,
                                 dtype=dtype, device=device, operations=operations)

    def forward(self, img: Tensor, txt: Tensor, vec, pe: Tensor, attn_mask=None) -> tuple[Tensor, Tensor]:
        if self.modulation:
            img_mod1, img_mod2 = self.img_mod(vec)
            txt_mod1, txt_mod2 = self.txt_mod(vec)
        else:
            (img_mod1, img_mod2), (txt_mod1, txt_mod2) = vec

        # prepare image for attention
        img_modulated = self.img_norm1(img)
        img_modulated = apply_mod(img_modulated, (1 + img_mod1.scale), img_mod1.shift)
        img_qkv = self.img_attn.qkv(img_modulated)
        del img_modulated
        img_q, img_k, img_v = img_qkv.view(img_qkv.shape[0], img_qkv.shape[1], 3, self.num_heads, -1).permute(2, 0, 3, 1, 4)
        del img_qkv
        img_q, img_k = self.img_attn.norm(img_q, img_k, img_v)

        # prepare txt for attention
        txt_modulated = self.txt_norm1(txt)
        txt_modulated = apply_mod(txt_modulated, (1 + txt_mod1.scale), txt_mod1.shift)
        txt_qkv = self.txt_attn.qkv(txt_modulated)
        del txt_modulated
        txt_q, txt_k, txt_v = txt_qkv.view(txt_qkv.shape[0], txt_qkv.shape[1], 3, self.num_heads, -1).permute(2, 0, 3, 1, 4)
        del txt_qkv
        txt_q, txt_k = self.txt_attn.norm(txt_q, txt_k, txt_v)

        # run attention over concatenated txt+img sequence
        q = torch.cat((txt_q, img_q), dim=2)
        del txt_q, img_q
        k = torch.cat((txt_k, img_k), dim=2)
        del txt_k, img_k
        v = torch.cat((txt_v, img_v), dim=2)
        del txt_v, img_v
        attn = attention(q, k, v, pe=pe, mask=attn_mask)
        del q, k, v
        txt_attn, img_attn = attn[:, : txt.shape[1]], attn[:, txt.shape[1]:]

        # image blocks
        img = img + apply_mod(self.img_attn.proj(img_attn), img_mod1.gate, None)
        del img_attn
        img = img + apply_mod(self.img_mlp(apply_mod(self.img_norm2(img), (1 + img_mod2.scale), img_mod2.shift)), img_mod2.gate, None)

        # text blocks
        txt = txt + apply_mod(self.txt_attn.proj(txt_attn), txt_mod1.gate, None)
        del txt_attn
        txt = txt + apply_mod(self.txt_mlp(apply_mod(self.txt_norm2(txt), (1 + txt_mod2.scale), txt_mod2.shift)), txt_mod2.gate, None)

        if txt.dtype == torch.float16:
            txt = torch.nan_to_num(txt, nan=0.0, posinf=65504, neginf=-65504)
        return img, txt


class SingleStreamBlock(nn.Module):
    """Parallel-linear DiT block with fused qkv + MLP."""

    def __init__(self, hidden_size: int, num_heads: int, mlp_ratio: float = 4.0, qk_scale: float | None = None,
                 modulation=True, mlp_silu_act=False, bias=True, dtype=None, device=None, operations=None):
        super().__init__()
        self.hidden_dim = hidden_size
        self.num_heads = num_heads
        head_dim = hidden_size // num_heads
        self.scale = qk_scale or head_dim**-0.5

        self.mlp_hidden_dim = int(hidden_size * mlp_ratio)
        self.mlp_hidden_dim_first = self.mlp_hidden_dim
        if mlp_silu_act:
            self.mlp_hidden_dim_first = int(hidden_size * mlp_ratio * 2)
            self.mlp_act = SiLUActivation()
        else:
            self.mlp_act = nn.GELU(approximate="tanh")

        # qkv and mlp_in
        self.linear1 = operations.Linear(hidden_size, hidden_size * 3 + self.mlp_hidden_dim_first, bias=bias, dtype=dtype, device=device)
        # proj and mlp_out
        self.linear2 = operations.Linear(hidden_size + self.mlp_hidden_dim, hidden_size, bias=bias, dtype=dtype, device=device)
        self.norm = QKNorm(head_dim, dtype=dtype, device=device, operations=operations)
        self.hidden_size = hidden_size
        self.pre_norm = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)

        if modulation:
            self.modulation = Modulation(hidden_size, double=False, dtype=dtype, device=device, operations=operations)
        else:
            self.modulation = None

    def forward(self, x: Tensor, vec, pe: Tensor, attn_mask=None) -> Tensor:
        if self.modulation is not None:
            mod, _ = self.modulation(vec)
        else:
            mod = vec

        qkv, mlp = torch.split(
            self.linear1(apply_mod(self.pre_norm(x), (1 + mod.scale), mod.shift)),
            [3 * self.hidden_size, self.mlp_hidden_dim_first], dim=-1,
        )
        q, k, v = qkv.view(qkv.shape[0], qkv.shape[1], 3, self.num_heads, -1).permute(2, 0, 3, 1, 4)
        del qkv
        q, k = self.norm(q, k, v)

        attn = attention(q, k, v, pe=pe, mask=attn_mask)
        del q, k, v
        mlp = self.mlp_act(mlp)
        output = self.linear2(torch.cat((attn, mlp), 2))
        x = x + apply_mod(output, mod.gate, None)
        if x.dtype == torch.float16:
            x = torch.nan_to_num(x, nan=0.0, posinf=65504, neginf=-65504)
        return x


class LastLayer(nn.Module):
    def __init__(self, hidden_size: int, patch_size: int, out_channels: int, bias=True,
                 dtype=None, device=None, operations=None):
        super().__init__()
        self.norm_final = operations.LayerNorm(hidden_size, elementwise_affine=False, eps=1e-6, dtype=dtype, device=device)
        self.linear = operations.Linear(hidden_size, patch_size * patch_size * out_channels, bias=bias, dtype=dtype, device=device)
        self.adaLN_modulation = nn.Sequential(nn.SiLU(), operations.Linear(hidden_size, 2 * hidden_size, bias=bias, dtype=dtype, device=device))

    def forward(self, x: Tensor, vec: Tensor) -> Tensor:
        if vec.ndim == 2:
            vec = vec[:, None, :]
        shift, scale = self.adaLN_modulation(vec).chunk(2, dim=-1)
        x = apply_mod(self.norm_final(x), (1 + scale), shift)
        x = self.linear(x)
        return x
