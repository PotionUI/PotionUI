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


def situ(hidden: Tensor, gate: Tensor) -> Tensor:
    return (4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)) * (25.0 * torch.tanh(hidden / 25.0))


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


def rotate(x: Tensor, rope: Tensor) -> Tensor:
    sin, cos = rope.chunk(2, -1)
    sin = torch.cat((sin, sin), -1).unsqueeze(-2)
    cos = torch.cat((cos, cos), -1).unsqueeze(-2)
    first, second = x.chunk(2, -1)
    return x * cos + torch.cat((-second, first), -1) * sin


def attend(q: Tensor, k: Tensor, v: Tensor, rope: Tensor, heads: int) -> Tensor:
    shape = (*q.shape[:2], heads, q.shape[-1] // heads)
    q = rotate(q.reshape(shape), rope).transpose(1, 2)
    k = rotate(k.reshape(shape), rope).transpose(1, 2)
    v = v.reshape(shape).transpose(1, 2)
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

    def forward(self, x: Tensor, rope: Tensor) -> Tensor:
        q, k, v = self.qkv(self.norm1(x)).chunk(3, -1)
        q = self.q_norm(q.unflatten(-1, (self.heads, -1))).flatten(2)
        k = self.k_norm(k.unflatten(-1, (self.heads, -1))).flatten(2)
        x = x + self.proj(attend(q, k, v, rope, self.heads))
        y = self.norm2(x)
        return x + self.ffn_down(situ(self.ffn_up(y), self.ffn_gate(y)))


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
        self.proj = nn.ModuleDict({
            "qkv": operations.Linear(dim, 3 * dim, bias=False, **kw),
            "o": operations.Linear(dim, dim, bias=False, **kw),
            "ffn_up_gate": operations.Linear(dim, 2 * ffn_dim, bias=False, **kw),
            "ffn_down": operations.Linear(ffn_dim, dim, bias=False, **kw),
        })

    def forward(self, x: Tensor, rope: Tensor, modulation: Tensor) -> Tensor:
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = modulation.unbind(1)
        y = self.norm1(x) * (1 + scale_a[:, None]) + shift_a[:, None]
        q, k, v = self.proj["qkv"](y).chunk(3, -1)
        q = self.q_norm(q.unflatten(-1, (self.heads, -1))).flatten(2)
        k = self.k_norm(k.unflatten(-1, (self.heads, -1))).flatten(2)
        x = x + self.proj["o"](attend(q, k, v, rope, self.heads)) * gate_a[:, None]
        y = self.norm2(x) * (1 + scale_f[:, None]) + shift_f[:, None]
        hidden, gate = self.proj["ffn_up_gate"](y).chunk(2, -1)
        return x + self.proj["ffn_down"](situ(hidden, gate)) * gate_f[:, None]
