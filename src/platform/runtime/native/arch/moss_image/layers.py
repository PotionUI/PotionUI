from __future__ import annotations

import torch
import torch.nn as nn
from torch import Tensor

from ...cancellation import raise_if_cancelled
from ...kernels import attention as _attention

EPS = 1e-6


class RMSNorm(nn.Module):
    def __init__(self, dim: int, dtype=None, device=None) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.empty(dim, dtype=dtype, device=device))

    def forward(self, x: Tensor) -> Tensor:
        xf = x.float()
        normed = xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + EPS)
        return normed.to(x.dtype) * self.weight


def rms_args(norm: RMSNorm) -> dict:
    return {"norm": "rms_fp32", "eps": EPS, "weight": norm.weight, "weight_cast": False}


def rope_bands(head_dim: int, base: float) -> Tensor:
    bands = head_dim // 8
    exponent = torch.arange(bands, device="cpu", dtype=torch.float32) / bands
    return 1.0 / (base**exponent)


def rope_table(height: int, width: int, text_length: int, bands: Tensor, device) -> Tensor:
    rows, cols = torch.meshgrid(torch.arange(height, device=device), torch.arange(width, device=device), indexing="ij")
    image = torch.stack((torch.zeros_like(rows), torch.full_like(rows, text_length), rows, cols), -1).flatten(0, 1).float()
    image[:, 2] -= (height - 1) / 2
    image[:, 3] -= (width - 1) / 2
    text = torch.zeros(text_length, 4, device=device, dtype=torch.float32)
    text[:, 1] = torch.arange(text_length, device=device)
    phase = torch.cat((image, text), 0).unsqueeze(-1) * bands.to(device)
    return torch.cat((phase.sin(), phase.cos()), -2).flatten(-2).unsqueeze(0)


def attend(q: Tensor, k: Tensor, v: Tensor, heads: int) -> Tensor:
    q = q.transpose(1, 2)
    k = k.transpose(1, 2)
    v = v.reshape(*v.shape[:2], heads, v.shape[-1] // heads).transpose(1, 2)
    out = _attention(q.to(v.dtype), k.to(v.dtype), v, heads=heads)
    return out.transpose(1, 2).flatten(2)


class TextBlock(nn.Module):
    def __init__(self, dim: int, ffn_dim: int, heads: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        kw = {"dtype": dtype, "device": device}
        self.heads = heads
        self.norm1 = RMSNorm(dim, **kw)
        self.norm2 = RMSNorm(dim, **kw)
        self.q_norm = RMSNorm(dim // heads, **kw)
        self.k_norm = RMSNorm(dim // heads, **kw)
        self.qkv = operations.Linear(dim, 3 * dim, **kw)
        self.proj = operations.Linear(dim, dim, **kw)
        self.ffn_gate = operations.Linear(dim, ffn_dim, **kw)
        self.ffn_up = operations.Linear(dim, ffn_dim, **kw)
        self.ffn_down = operations.Linear(ffn_dim, dim, **kw)
        self.operations = operations

    def forward(self, x: Tensor, rope: Tensor) -> Tensor:
        ops = self.operations
        q, k, v = self.qkv(self.norm1(x)).chunk(3, -1)
        q = ops.qk_norm_rope(q.unflatten(-1, (self.heads, -1)), **rms_args(self.q_norm), freqs=rope, layout="rotate_half")
        k = ops.qk_norm_rope(k.unflatten(-1, (self.heads, -1)), **rms_args(self.k_norm), freqs=rope, layout="rotate_half")
        x = x + self.proj(attend(q, k, v, self.heads))
        y = self.norm2(x)
        return x + self.ffn_down(ops.act_gate(self.ffn_gate(y), mode="situ_split", up=self.ffn_up(y)))


class TextProjection(nn.Module):
    def __init__(self, text_dim: int, dim: int, ffn_dim: int, heads: int, blocks: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.proj = operations.Linear(text_dim, dim, dtype=dtype, device=device)
        self.blocks = nn.ModuleList(
            [TextBlock(dim, ffn_dim, heads, operations, dtype=dtype, device=device) for _ in range(blocks)]
        )

    def forward(self, x: Tensor, rope: Tensor) -> Tensor:
        x = self.proj(x)
        for i, block in enumerate(self.blocks):
            raise_if_cancelled(i)
            x = block(x, rope)
        return x


class Block(nn.Module):
    def __init__(self, dim: int, ffn_dim: int, heads: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        kw = {"dtype": dtype, "device": device}
        self.heads = heads
        self.norm1 = RMSNorm(dim, **kw)
        self.norm2 = RMSNorm(dim, **kw)
        self.q_norm = RMSNorm(dim // heads, **kw)
        self.k_norm = RMSNorm(dim // heads, **kw)
        self.operations = operations
        self.proj = nn.ModuleDict({
            "qkv": operations.Linear(dim, 3 * dim, bias=False, **kw),
            "o": operations.Linear(dim, dim, bias=False, **kw),
            "ffn_up_gate": operations.Linear(dim, 2 * ffn_dim, bias=False, **kw),
            "ffn_down": operations.Linear(ffn_dim, dim, bias=False, **kw),
        })

    def forward(self, x: Tensor, rope: Tensor, modulation: Tensor) -> Tensor:
        ops = self.operations
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = modulation.unbind(1)
        y = ops.norm_modulate(x, **rms_args(self.norm1), scale=scale_a[:, None], shift=shift_a[:, None])
        q, k, v = self.proj["qkv"](y).chunk(3, -1)
        q = ops.qk_norm_rope(q.unflatten(-1, (self.heads, -1)), **rms_args(self.q_norm), freqs=rope, layout="rotate_half")
        k = ops.qk_norm_rope(k.unflatten(-1, (self.heads, -1)), **rms_args(self.k_norm), freqs=rope, layout="rotate_half")
        x = ops.gated_residual(x, self.proj["o"](attend(q, k, v, self.heads)), gate_a[:, None])
        y = ops.norm_modulate(x, **rms_args(self.norm2), scale=scale_f[:, None], shift=shift_f[:, None])
        situ = ops.act_gate(self.proj["ffn_up_gate"](y), mode="situ")
        return ops.gated_residual(x, self.proj["ffn_down"](situ), gate_f[:, None])
