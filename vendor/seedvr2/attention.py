# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit_v2 attention (flash_attn_varlen_func usage) @
# unknown; vendored ~2025 (moved into vendor/seedvr2/ from
# src/platform/runtime/native/arch/seedvr2/ as part of the license-relocation
# workstream, BE-97).
# License: Apache-2.0 (see LICENSE).
# Local modification (BE-97): the native engine's attention-kernel dispatcher
# (src/platform/runtime/native/attention.py) can't be imported here — this
# package must not depend on src. The fallback path calls a module-level
# backend hook instead; src wires it via set_attention_backend() (see
# arch/seedvr2/model.py, the one importer that constructs SeedVR2/SeedVR27B).

"""Variable-length attention for NaDiT windows, on the native attention seam.

The reference packs every (video-window + full-text) block into one long
sequence and runs ``flash_attn_varlen_func`` with ``cu_seqlens`` boundaries.
When real ``flash_attn`` is installed and the inputs qualify (CUDA, fp16/bf16),
we call it directly — its layout is exactly our packed ``(total, H, D)``
q/k/v, so no reshape is needed beyond making ``cu_seqlens`` int32 and living on
the same device as ``q``. Otherwise this falls back to reimplementing the same
semantics on top of the project's injected attention backend (sage/flash/sdpa
with an sdpa floor): split the packed ``(L, H, D)`` q/k/v at the cumulative
sequence lengths and run each block as an independent full-attention (no mask —
every token in a block attends every other, exactly what the joined window+text
sequence wants). Both paths are numerically equivalent; the fallback's seam
lets sage2 accelerate the per-block SDPA when flash-varlen itself isn't
available.
"""

from __future__ import annotations

import logging
from typing import Callable

import torch

logger = logging.getLogger(__name__)

# Injected by src at import time (see the module docstring). None until then —
# calling varlen_attention's fallback path before wiring raises rather than
# silently no-oping.
_attention_backend: "Callable[..., torch.Tensor] | None" = None


def set_attention_backend(fn: "Callable[..., torch.Tensor]") -> None:
    """Wire the attention-kernel dispatcher the fallback path calls into.

    ``fn(q, k, v, mask=...) -> Tensor`` — same contract as
    ``src.platform.runtime.native.attention.attention``. Idempotent; safe to
    call more than once (later calls replace the backend).
    """
    global _attention_backend
    _attention_backend = fn


# Module-level probe cache for the real flash-varlen kernel. Separate from
# ``src.platform.runtime.native.attention`` because that dispatcher's contract is
# head-split (B, H, L, D) plain attention; flash_attn_varlen_func wants packed
# (total, H, D) with cu_seqlens, a different-enough shape story to keep local.
_flash_varlen_func = None
_flash_varlen_probed = False

# Set on the first runtime failure of the real kernel (e.g. an unsupported GPU
# that still lets the module import, or a head_dim past this flash-attn
# build's kernel limit — both raise from INSIDE flash_attn_varlen_func, not at
# import time, so the import-only probe above can't catch them). Once set,
# every subsequent call falls back to the per-block dispatcher path directly
# without retrying a call already proven to fail on this process/hardware.
_flash_varlen_broken = False
_flash_varlen_warned = False


def _probe_flash_varlen():
    global _flash_varlen_func, _flash_varlen_probed
    if _flash_varlen_probed:
        return _flash_varlen_func
    _flash_varlen_probed = True
    try:
        from flash_attn import flash_attn_varlen_func

        _flash_varlen_func = flash_attn_varlen_func
    except Exception:  # noqa: BLE001 — not installed, or an import-time failure
        _flash_varlen_func = None
    return _flash_varlen_func


def reset_flash_varlen_cache() -> None:
    """Drop the cached probe (tests)."""
    global _flash_varlen_func, _flash_varlen_probed, _flash_varlen_broken, _flash_varlen_warned
    _flash_varlen_func = None
    _flash_varlen_probed = False
    _flash_varlen_broken = False
    _flash_varlen_warned = False


def _flash_varlen_available(q: torch.Tensor) -> bool:
    return (
        not _flash_varlen_broken
        and _probe_flash_varlen() is not None
        and q.is_cuda
        and q.dtype in (torch.float16, torch.bfloat16)
    )


def _run_flash_varlen(q, k, v, cu_seqlens_q, cu_seqlens_k):
    flash_attn_varlen_func = _flash_varlen_func
    cu_q = cu_seqlens_q.to(device=q.device, dtype=torch.int32)
    cu_k = cu_seqlens_k.to(device=q.device, dtype=torch.int32)
    max_seqlen_q = int((cu_q[1:] - cu_q[:-1]).max().item())
    max_seqlen_k = int((cu_k[1:] - cu_k[:-1]).max().item())
    logger.debug(
        "seedvr2 varlen_attention: flash_attn_varlen_func kernel in use (blocks=%d)",
        cu_q.numel() - 1,
    )
    return flash_attn_varlen_func(
        q, k, v,
        cu_seqlens_q=cu_q,
        cu_seqlens_k=cu_k,
        max_seqlen_q=max_seqlen_q,
        max_seqlen_k=max_seqlen_k,
        causal=False,
    )


def _varlen_attention_fallback(q, k, v, cu_seqlens_q, cu_seqlens_k):
    if _attention_backend is None:
        raise RuntimeError(
            "seedvr2 varlen_attention: no attention backend wired — call set_attention_backend() first"
        )
    # Interior boundaries drive the split; keep them on CPU for tensor_split.
    q_bounds = cu_seqlens_q[1:-1].to(dtype=torch.long, device="cpu")
    k_bounds = cu_seqlens_k[1:-1].to(dtype=torch.long, device="cpu")
    q_blocks = torch.tensor_split(q, q_bounds, dim=0)
    k_blocks = torch.tensor_split(k, k_bounds, dim=0)
    v_blocks = torch.tensor_split(v, k_bounds, dim=0)

    outs = []
    for qi, ki, vi in zip(q_blocks, k_blocks, v_blocks):
        # (seq, h, d) -> (1, h, seq, d) for the (B, H, L, D) dispatcher contract.
        qi = qi.transpose(0, 1).unsqueeze(0)
        ki = ki.transpose(0, 1).unsqueeze(0)
        vi = vi.transpose(0, 1).unsqueeze(0)
        oi = _attention_backend(qi, ki, vi, mask=None)  # (1, h, seq, d)
        outs.append(oi.squeeze(0).transpose(0, 1))  # (seq, h, d)
    return torch.cat(outs, dim=0)


def varlen_attention(
    q: torch.Tensor,  # (L, H, D)
    k: torch.Tensor,
    v: torch.Tensor,
    cu_seqlens_q: torch.Tensor,  # (nblocks + 1,) int, cumulative q lengths
    cu_seqlens_k: torch.Tensor,
) -> torch.Tensor:
    if _flash_varlen_available(q):
        try:
            return _run_flash_varlen(q, k, v, cu_seqlens_q, cu_seqlens_k)
        except Exception as e:  # noqa: BLE001 — any kernel failure (unsupported
            # capability, head_dim past this build's limit, etc.) must fall back
            # to the per-block dispatcher path below rather than crash the
            # generation; the import-time probe in _probe_flash_varlen can't
            # catch these since they only raise from inside the actual kernel call.
            global _flash_varlen_broken, _flash_varlen_warned
            _flash_varlen_broken = True
            if not _flash_varlen_warned:
                _flash_varlen_warned = True
                logger.warning(
                    "seedvr2 varlen_attention: flash_attn_varlen_func failed at runtime (%s) "
                    "— falling back to the per-block dispatcher path for the rest of this process",
                    e,
                )

    return _varlen_attention_fallback(q, k, v, cu_seqlens_q, cu_seqlens_k)
