from __future__ import annotations

from collections.abc import Sequence

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from . import casting

NORM_KINDS = ("layer", "rms", "rms_fp32")
GATE_FNS = ("tanh",)
ACT_MODES = ("swiglu", "swiglu_split", "gelu_tanh", "gelu")


def _norm_weight(x: Tensor, weight: Tensor | None, offset: float, cast: bool) -> Tensor | None:
    if weight is None:
        return None
    if offset:
        weight = weight + offset
    if cast:
        weight = casting.cast_to(weight, x.dtype, x.device)
    return weight


def _normalize(
    x: Tensor,
    norm: str | None,
    eps: float | None,
    weight: Tensor | None,
    weight_offset: float,
    weight_cast: bool,
) -> Tensor:
    if norm is None:
        return x
    w = _norm_weight(x, weight, weight_offset, weight_cast)
    if norm == "layer":
        return F.layer_norm(x, (x.shape[-1],), w, None, eps)
    if norm == "rms":
        return F.rms_norm(x, (x.shape[-1],), w, eps)
    if norm == "rms_fp32":
        xf = x.float()
        out = (xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + eps)).to(x.dtype)
        return out if w is None else out * w
    raise ValueError(f"unknown norm kind {norm!r}; expected one of {NORM_KINDS}")


def _rows(table: Tensor | None, index: Tensor | None) -> Tensor | None:
    if table is None or index is None:
        return table
    return table.index_select(-2, index)


def _offset_scale(scale: Tensor, offset: float) -> Tensor:
    return offset + scale if offset else scale


def _modulate(h: Tensor, scale: Tensor, shift: Tensor | None, addcmul: bool, inplace: bool) -> Tensor:
    if shift is None:
        return h.mul_(scale) if inplace else h * scale
    if inplace:
        return h.mul_(scale).add_(shift)
    if addcmul:
        return torch.addcmul(shift, h, scale)
    return h * scale + shift


def norm_modulate(
    x: Tensor,
    *,
    scale: Tensor | Sequence[Tensor],
    shift: Tensor | Sequence[Tensor] | None = None,
    norm: str | None = None,
    eps: float | None = 1e-6,
    weight: Tensor | None = None,
    weight_offset: float = 0.0,
    weight_cast: bool = True,
    scale_offset: float = 1.0,
    index: Tensor | None = None,
    spans: Sequence[int] | None = None,
    addcmul: bool = False,
    inplace: bool = False,
) -> Tensor:
    h = _normalize(x, norm, eps, weight, weight_offset, weight_cast)
    if spans is None:
        return _modulate(
            h,
            _rows(_offset_scale(scale, scale_offset), index),
            _rows(shift, index),
            addcmul,
            inplace,
        )
    shifts = shift if shift is not None else (None,) * len(spans)
    parts = []
    start = 0
    for length, sc, sh in zip(spans, scale, shifts, strict=True):
        parts.append(_modulate(h[:, start : start + length], _offset_scale(sc, scale_offset), sh, addcmul, False))
        start += length
    return parts[0] if len(parts) == 1 else torch.cat(parts, dim=1)


def _gate(gate: Tensor, gate_fn: str | None) -> Tensor:
    if gate_fn is None:
        return gate
    if gate_fn == "tanh":
        return gate.tanh()
    raise ValueError(f"unknown gate function {gate_fn!r}; expected one of {GATE_FNS}")


def gated_residual(
    x: Tensor,
    y: Tensor,
    gate: Tensor | Sequence[Tensor],
    *,
    gate_fn: str | None = None,
    norm: str | None = None,
    eps: float | None = 1e-6,
    weight: Tensor | None = None,
    weight_cast: bool = True,
    index: Tensor | None = None,
    spans: Sequence[int] | None = None,
    addcmul: bool = False,
) -> Tensor:
    y = _normalize(y, norm, eps, weight, 0.0, weight_cast)
    if spans is None:
        g = _rows(_gate(gate, gate_fn), index)
        return torch.addcmul(x, y, g) if addcmul else x + y * g
    parts = []
    start = 0
    for length, g in zip(spans, gate, strict=True):
        parts.append(y[:, start : start + length] * _gate(g, gate_fn))
        start += length
    return x + (parts[0] if len(parts) == 1 else torch.cat(parts, dim=1))


def norm_args(module: nn.Module) -> dict:
    if isinstance(module, nn.RMSNorm):
        kind = "rms"
    elif isinstance(module, nn.LayerNorm) and module.bias is None:
        kind = "layer"
    else:
        raise TypeError(f"norm_args takes an RMSNorm or a bias-free LayerNorm, got {type(module).__name__}")
    return {
        "norm": kind,
        "eps": module.eps,
        "weight": module.weight,
        "weight_cast": getattr(module, "comfy_cast_weights", False),
    }


def act_gate(h: Tensor, *, mode: str, up: Tensor | None = None) -> Tensor:
    if mode == "swiglu":
        gate, value = h.chunk(2, dim=-1)
        return F.silu(gate) * value
    if mode == "swiglu_split":
        return F.silu(h) * up
    if mode == "gelu_tanh":
        return F.gelu(h, approximate="tanh")
    if mode == "gelu":
        return F.gelu(h)
    raise ValueError(f"unknown activation mode {mode!r}; expected one of {ACT_MODES}")


class ActGate(nn.Module):
    def __init__(self, mode: str) -> None:
        super().__init__()
        if mode not in ACT_MODES or mode == "swiglu_split":
            raise ValueError(f"ActGate takes one input; mode must be one of {ACT_MODES[:1] + ACT_MODES[2:]}")
        self.mode = mode

    def forward(self, x: Tensor) -> Tensor:
        return act_gate(x, mode=self.mode)

    def extra_repr(self) -> str:
        return f"mode={self.mode!r}"
