from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, List, Optional

import torch
import torch.nn as nn

from .key_mapping import LoraDelta

MASKED_DELTAS_ATTR = "lora_masked_deltas"
_HOOK_ATTR = "_native_lora_masked_hook"

_ROW_MASK: ContextVar[Optional[torch.Tensor]] = ContextVar("lora_row_mask", default=None)


@dataclass(frozen=True)
class RowMaskTarget:
    mode: str
    keep_columns: Optional[torch.Tensor] = None


FULL = RowMaskTarget("full")
ROWS = RowMaskTarget("rows")
SKIP = RowMaskTarget("skip")

RowMaskPolicy = Callable[[str], RowMaskTarget]


def row_mask_policy(module: nn.Module) -> RowMaskPolicy:
    policy = getattr(module, "lora_row_mask_target", None)
    if policy is None:
        raise ValueError(
            f"{type(module).__name__} cannot keep a LoRA out of part of its sequence; "
            "apply this LoRA to every row instead"
        )
    return policy


@contextmanager
def lora_row_mask(mask: torch.Tensor) -> Iterator[None]:
    token = _ROW_MASK.set(mask.reshape(-1))
    try:
        yield
    finally:
        _ROW_MASK.reset(token)


@contextmanager
def lora_row_window(start: int, length: int) -> Iterator[None]:
    mask = _ROW_MASK.get()
    if mask is None:
        yield
        return
    token = _ROW_MASK.set(mask.narrow(0, start, length))
    try:
        yield
    finally:
        _ROW_MASK.reset(token)


def _current_mask(rows: int, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    mask = _ROW_MASK.get()
    if mask is None:
        raise RuntimeError("a row-masked LoRA ran outside a lora_row_mask context")
    span = mask.shape[0]
    if span == 0 or rows % span:
        raise RuntimeError(f"the LoRA row mask covers {span} rows but the input has {rows}")
    mask = mask.to(device=device, dtype=dtype)
    if rows != span:
        mask = mask.repeat(rows // span)
    return mask.unsqueeze(1)


def add_masked_branch(linear: nn.Module, x: torch.Tensor, out: torch.Tensor) -> torch.Tensor:
    deltas = getattr(linear, MASKED_DELTAS_ATTR, None)
    if not deltas:
        return out
    if torch.is_grad_enabled():
        out = out.clone()
    x2d = x.reshape(-1, x.shape[-1]).to(out.dtype)
    out2d = out.reshape(-1, out.shape[-1])
    mask = _current_mask(x2d.shape[0], out.device, out.dtype)
    masked_x = None
    for d in deltas:
        target = out2d
        if d.target_slice is not None:
            _dim, start, length = d.target_slice
            target = out2d[:, start:start + length]
        up = d.up.to(device=out.device, dtype=torch.float32)
        down = d.down.to(device=out.device, dtype=torch.float32)
        if d.kron:
            if masked_x is None:
                masked_x = x2d * mask
            weight = torch.kron(up, down).reshape(target.shape[1], x2d.shape[1])
            weight = (weight * (float(d.scale) * float(d.alpha))).to(out.dtype)
            target.add_(masked_x @ weight.t())
        else:
            coeff = float(d.scale) * float(d.alpha) / d.down.shape[0]
            hidden = (x2d @ down.to(out.dtype).t()) * mask
            target.addmm_(hidden, up.to(out.dtype).t(), alpha=coeff)
    return out


def _masked_hook(linear: nn.Module, args: tuple, output: torch.Tensor) -> torch.Tensor:
    return add_masked_branch(linear, args[0], output)


def attach_masked_deltas(linear: nn.Module, deltas: List[LoraDelta]) -> None:
    existing = getattr(linear, MASKED_DELTAS_ATTR, None)
    if existing:
        existing.extend(deltas)
        return
    setattr(linear, MASKED_DELTAS_ATTR, list(deltas))
    setattr(linear, _HOOK_ATTR, linear.register_forward_hook(_masked_hook))


def masked_delta_count(linear: nn.Module) -> "int | None":
    deltas = getattr(linear, MASKED_DELTAS_ATTR, None)
    return len(deltas) if deltas else None


def truncate_masked_deltas(linear: nn.Module, length: "int | None") -> None:
    deltas = getattr(linear, MASKED_DELTAS_ATTR, None)
    if deltas is None:
        return
    if length:
        setattr(linear, MASKED_DELTAS_ATTR, deltas[:length])
        return
    getattr(linear, _HOOK_ATTR).remove()
    delattr(linear, _HOOK_ATTR)
    delattr(linear, MASKED_DELTAS_ATTR)


def keep_output_columns(deltas: List[LoraDelta], keep: torch.Tensor) -> List[LoraDelta]:
    kept: List[LoraDelta] = []
    for d in deltas:
        if d.kron:
            continue
        rows = keep
        if d.target_slice is not None:
            _dim, start, length = d.target_slice
            rows = keep[start:start + length]
        if not bool(rows.any()):
            continue
        up = d.up * rows.to(device=d.up.device, dtype=d.up.dtype).unsqueeze(1)
        kept.append(LoraDelta(down=d.down, up=up, alpha=d.alpha, scale=d.scale,
                              target_slice=d.target_slice, kron=d.kron))
    return kept


def confine_bias_deltas(module: nn.Module, bias_deltas: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    policy = row_mask_policy(module)
    confined: Dict[str, torch.Tensor] = {}
    for stem, delta in bias_deltas.items():
        target = policy(stem)
        if target.mode == "skip":
            continue
        if target.mode == "rows":
            raise ValueError(f"a bias delta on {stem} cannot be kept out of masked rows")
        if target.keep_columns is not None:
            delta = delta * target.keep_columns.to(device=delta.device, dtype=delta.dtype)
        confined[stem] = delta
    return confined
