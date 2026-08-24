"""Small NN primitives shared by :mod:`.lm` and :mod:`.depth_decoder`.

Extracted from ``text_encoders/qwen3.py``'s ``_RMSNorm``/``_rotate_half``/
``_apply_rope`` (same math, same ``operations`` substrate) rather than
imported from it — per the port plan, ``qwen3.py``'s encode path is not to be
touched, and its private helpers aren't a stable cross-package import surface.
A private module of this package's own, not part of :mod:`.` 's public API.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...text_encoders._functional import rms_norm


class GatedMLP(nn.Module):
    """SwiGLU MLP shared by the global LLM and the depth decoder — same math,
    different widths (``hidden_size``/``intermediate_size``) and independently
    detected fusion (``merged``: one ``gate_up_proj`` vs. split
    ``gate_proj``/``up_proj`` — see the module-config layout booleans).
    """

    def __init__(self, hidden_size: int, intermediate_size: int, merged: bool, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.merged = merged
        if merged:
            self.gate_up_proj = operations.Linear(hidden_size, 2 * intermediate_size, bias=False, device=device, dtype=dtype)
        else:
            self.gate_proj = operations.Linear(hidden_size, intermediate_size, bias=False, device=device, dtype=dtype)
            self.up_proj = operations.Linear(hidden_size, intermediate_size, bias=False, device=device, dtype=dtype)
        self.down_proj = operations.Linear(intermediate_size, hidden_size, bias=False, device=device, dtype=dtype)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.merged:
            gate, up = self.gate_up_proj(x).chunk(2, dim=-1)
        else:
            gate, up = self.gate_proj(x), self.up_proj(x)
        return self.down_proj(F.silu(gate) * up)


class RMSNorm(nn.Module):
    """RMS norm with an owned weight (every ``*_layernorm``/``q_norm``/``k_norm``)."""

    def __init__(self, dim: int, eps: float, device=None, dtype=None) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.empty(dim, device=device, dtype=dtype))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return rms_norm(x, self.weight, self.eps)


def rotate_half(x: torch.Tensor) -> torch.Tensor:
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2:]
    return torch.cat((-x2, x1), dim=-1)


def apply_rope(xq: torch.Tensor, xk: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor):
    org = xq.dtype
    q = (xq * cos) + (rotate_half(xq) * sin)
    k = (xk * cos) + (rotate_half(xk) * sin)
    return q.to(org), k.to(org)


def module_device(module: nn.Module) -> torch.device:
    """The device a module's real tensors live on.

    Same rationale as ``qwen3.py``'s private ``_module_device``: a quantized
    ``Linear``/``Embedding`` may have freed its float ``.weight`` to ``None``
    after loading its dequant state into other buffers, so this walks every
    parameter/buffer rather than reading one attribute unconditionally.
    """
    for p in module.parameters():
        if p is not None:
            return p.device
    for b in module.buffers():
        if b is not None:
            return b.device
    raise RuntimeError(f"{module.__class__.__name__} has no tensors to read a device from")
