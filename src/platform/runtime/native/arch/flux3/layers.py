from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from ...kernels import attention as _attention

Modulation = tuple[int, Tensor, Tensor, Tensor]
Rope = tuple[Tensor, Tensor]


def rope_tables(ids: Tensor, axes_dim: tuple[int, ...], theta: int) -> Rope:
    angles = []
    for axis, dim in enumerate(axes_dim):
        scale = torch.arange(0, dim, 2, dtype=torch.float64, device=ids.device) / dim
        omega = 1.0 / (theta**scale)
        angles.append(ids[..., axis].double()[..., None] * omega)
    angle = torch.cat(angles, dim=-1)
    return torch.cos(angle).float()[None, None], torch.sin(angle).float()[None, None]


def rmsnorm_rope(x: Tensor, scale: Tensor, rope: Rope, eps: float = 1e-6) -> Tensor:
    b, h, n, d = x.shape
    x_f = x.float()
    normed = x_f * x_f.pow(2).mean(-1, keepdim=True).add(eps).rsqrt() * scale.float()
    pairs = normed.reshape(b, h, n, d // 2, 2)
    cos, sin = rope
    first, second = pairs[..., 0], pairs[..., 1]
    rotated = torch.stack((cos * first - sin * second, sin * first + cos * second), dim=-1)
    return rotated.reshape(b, h, n, d).to(x.dtype)


class RMSNormScale(nn.Module):
    def __init__(self, dim: int, dtype=None, device=None) -> None:
        super().__init__()
        self.scale = nn.Parameter(torch.empty(dim, dtype=dtype, device=device))


class QKNorm(nn.Module):
    def __init__(self, dim: int, dtype=None, device=None) -> None:
        super().__init__()
        self.query_norm = RMSNormScale(dim, dtype=dtype, device=device)
        self.key_norm = RMSNormScale(dim, dtype=dtype, device=device)

    def forward(self, q: Tensor, k: Tensor, rope: Rope) -> tuple[Tensor, Tensor]:
        return rmsnorm_rope(q, self.query_norm.scale, rope), rmsnorm_rope(k, self.key_norm.scale, rope)


class MLPEmbedder(nn.Module):
    def __init__(self, in_dim: int, hidden_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.in_layer = operations.Linear(in_dim, hidden_dim, bias=False, dtype=dtype, device=device)
        self.out_layer = operations.Linear(hidden_dim, hidden_dim, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        return self.out_layer(F.silu(self.in_layer(x)))


class ModeModulation(nn.Module):
    def __init__(self, dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.lin = operations.Linear(dim, 3 * dim, bias=False, dtype=dtype, device=device)

    def forward(self, vec: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        shift, scale, gate = self.lin(F.silu(vec)).chunk(3, dim=-1)
        return shift[:, None], scale[:, None], gate[:, None]


class LastLayer(nn.Module):
    def __init__(self, hidden: int, out_channels: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.operations = operations
        self.linear = operations.Linear(hidden, out_channels, bias=False, dtype=dtype, device=device)
        self.adaLN_modulation = nn.Sequential(
            nn.SiLU(),
            operations.Linear(hidden, 2 * hidden, bias=False, dtype=dtype, device=device),
        )

    def forward(self, x: Tensor, vec: Tensor) -> Tensor:
        shift, scale = self.adaLN_modulation(vec).chunk(2, dim=-1)
        return self.linear(self.operations.norm_modulate(
            x, norm="layer", eps=1e-6, scale=scale[:, None], shift=shift[:, None], addcmul=True,
        ))


class Block(nn.Module):
    def __init__(
        self, hidden: int, heads: int, mlp_hidden: int, operations, dtype=None, device=None, fused_qkv: bool = False
    ) -> None:
        super().__init__()
        self.heads = heads
        self.fused_qkv = fused_qkv
        self.operations = operations
        if fused_qkv:
            self.qkv = operations.Linear(hidden, hidden * 3, bias=False, dtype=dtype, device=device)
        else:
            self.q_proj = operations.Linear(hidden, hidden, bias=False, dtype=dtype, device=device)
            self.k_proj = operations.Linear(hidden, hidden, bias=False, dtype=dtype, device=device)
            self.v_proj = operations.Linear(hidden, hidden, bias=False, dtype=dtype, device=device)
        self.mlp_in = operations.Linear(hidden, mlp_hidden * 2, bias=False, dtype=dtype, device=device)
        self.attn_out = operations.Linear(hidden, hidden, bias=False, dtype=dtype, device=device)
        self.mlp_out = operations.Linear(mlp_hidden, hidden, bias=False, dtype=dtype, device=device)
        self.norm = QKNorm(hidden // heads, dtype=dtype, device=device)

    def _split(self, t: Tensor) -> Tensor:
        b, n, _ = t.shape
        return t.reshape(b, n, self.heads, -1).transpose(1, 2)

    def forward(self, x: Tensor, rope: Rope, segments: list[Modulation]) -> Tensor:
        ops = self.operations
        spans = [length for length, _, _, _ in segments]
        x_mod = ops.norm_modulate(
            x, norm="layer", eps=1e-6, spans=spans, addcmul=True,
            scale=[scale for _, _, scale, _ in segments], shift=[shift for _, shift, _, _ in segments],
        )
        if self.fused_qkv:
            q, k, v = self.qkv(x_mod).chunk(3, dim=-1)
        else:
            q, k, v = self.q_proj(x_mod), self.k_proj(x_mod), self.v_proj(x_mod)
        q, k = self.norm(self._split(q), self._split(k), rope)
        v = self._split(v).to(q.dtype)
        attn = _attention(q, k, v, heads=self.heads)
        b, _, n, _ = attn.shape
        attn = attn.transpose(1, 2).reshape(b, n, -1)
        out = self.attn_out(attn) + self.mlp_out(ops.act_gate(self.mlp_in(x_mod), mode="swiglu"))
        return ops.gated_residual(x, out, [gate for _, _, _, gate in segments], spans=spans)
