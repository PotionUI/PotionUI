# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/flux/math.py @ unknown; vendored ~2025 (moved into
# vendor/gpl/comfyui/flux/ from src/platform/runtime/native/arch/flux/ as part
# of the license-relocation workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.
# Local modification (BE-97): the native engine's attention-kernel dispatcher
# (src/platform/runtime/native/attention.py) can't be imported here — this
# package must not depend on src. attention() calls a module-level backend
# hook instead; src wires it via set_attention_backend() (see arch/flux/
# model.py, the one importer that constructs Flux and therefore must run
# before any attention() call).

"""Rotary positional embedding + attention math for the Flux DiT.

Vendored from ComfyUI's ``comfy/ldm/flux/math.py`` and trimmed:

  * the ``comfy.quant_ops`` / ``comfy.model_management`` device special-cases
    (MPS / XPU / DirectML) are dropped — we run CPU / CUDA only;
  * ``optimized_attention`` (a dispatch over xformers / sub-quad / split
    kernels) is replaced by an injected backend callable (see
    :func:`set_attention_backend`) — the native engine's attention dispatcher
    (``src/platform/runtime/native/attention.py``), which defaults to
    ``torch.nn.functional.scaled_dot_product_attention`` (ComfyUI's
    ``attention_pytorch`` path) and transparently uses flash/sage when available.

Numerics on the default (sdpa) path are kept bit-for-bit with ComfyUI's pytorch
path so golden latent comparisons line up: RoPE is built in float64 then cast to
float32, attention runs in the activation dtype with ``is_causal=False``.
"""

from __future__ import annotations

from typing import Callable

import torch
from einops import rearrange
from torch import Tensor

# Injected by src at import time (see the module docstring). None until then —
# calling attention() before wiring raises rather than silently no-oping.
_attention_backend: "Callable[..., Tensor] | None" = None


def set_attention_backend(fn: "Callable[..., Tensor]") -> None:
    """Wire the attention-kernel dispatcher :func:`attention` calls into.

    ``fn(q, k, v, heads=..., mask=...) -> Tensor`` — same contract as
    ``src.platform.runtime.native.attention.attention``. Idempotent; safe to
    call more than once (later calls replace the backend).
    """
    global _attention_backend
    _attention_backend = fn


def attention(q: Tensor, k: Tensor, v: Tensor, pe: Tensor | None, mask: Tensor | None = None) -> Tensor:
    """Scaled-dot-product attention with RoPE applied to q/k.

    ``q``/``k``/``v`` are already head-split: shape ``(B, H, L, D)``. Returns
    ``(B, L, H*D)`` — the ``skip_reshape`` + reshaped-output contract of
    ComfyUI's ``attention_pytorch``.
    """
    if pe is not None:
        q, k = apply_rope(q, k, pe)

    b, heads, _, dim_head = q.shape
    if mask is not None:
        if mask.ndim == 2:
            mask = mask.unsqueeze(0)
        if mask.ndim == 3:
            mask = mask.unsqueeze(1)

    if _attention_backend is None:
        raise RuntimeError(
            "flux math_ops.attention: no backend wired — call set_attention_backend() first"
        )
    out = _attention_backend(q, k, v, heads=heads, mask=mask)
    return out.transpose(1, 2).reshape(b, -1, heads * dim_head)


def rope(pos: Tensor, dim: int, theta: int) -> Tensor:
    """Build the RoPE rotation tensor for one positional axis.

    Returns shape ``(..., dim//2, 2, 2)`` in float32 (built in float64 for
    numerical parity with ComfyUI).
    """
    assert dim % 2 == 0
    device = pos.device
    scale = torch.linspace(0, (dim - 2) / dim, steps=dim // 2, dtype=torch.float64, device=device)
    omega = 1.0 / (theta**scale)
    out = torch.einsum("...n,d->...nd", pos.to(dtype=torch.float32, device=device), omega)
    out = torch.stack([torch.cos(out), -torch.sin(out), torch.sin(out), torch.cos(out)], dim=-1)
    out = rearrange(out, "b n d (i j) -> b n d i j", i=2, j=2)
    return out.to(dtype=torch.float32, device=pos.device)


def apply_rope1(x: Tensor, freqs_cis: Tensor) -> Tensor:
    x_ = x.to(dtype=freqs_cis.dtype).reshape(*x.shape[:-1], -1, 1, 2)
    x_out = freqs_cis[..., 0] * x_[..., 0]
    x_out = x_out.addcmul(freqs_cis[..., 1], x_[..., 1])
    return x_out.reshape(*x.shape).type_as(x)


def apply_rope(xq: Tensor, xk: Tensor, freqs_cis: Tensor) -> tuple[Tensor, Tensor]:
    return apply_rope1(xq, freqs_cis), apply_rope1(xk, freqs_cis)
