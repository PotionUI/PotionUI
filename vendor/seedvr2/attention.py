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
# Local modification (PotionUI): the packed-block boundaries (``cu_seqlens``)
# are read once into a ``VarlenGeometry`` instead of being re-derived per
# attention block. The NaDiT builds its shape tensors on the compute device, so
# the reference's per-block ``(cu[1:] - cu[:-1]).max().item()`` and the
# fallback's per-block copy of the split points to the host are device→host
# syncs on every block of every step; the geometry pays them once, when the
# per-forward cache builds it (see layers.py's ``cache_win("varlen_geometry")``).

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

Both paths take their block boundaries from a :class:`VarlenGeometry`, which
resolves the ``cu_seqlens`` tensor to Python-side offsets/maxima once and keeps
the int32 device copy every block reuses. Callers may still pass a raw
``cu_seqlens`` tensor — it is resolved per call, which is what the geometry
exists to avoid.
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


class VarlenGeometry:
    """Block layout of one packed varlen sequence: offsets, maxima, device ``cu``.

    Built from a ``cu_seqlens`` tensor — one device→host read — and then read
    without touching the device again: ``max_seqlen`` and ``split_bounds`` are
    Python ints, and ``cu_on()`` hands back a cached int32 tensor per device.
    Holds nothing per block or per step, so a geometry stored in the
    per-forward ``Cache`` dies with that forward.
    """

    __slots__ = ("offsets", "lengths", "max_seqlen", "split_bounds", "_by_device")

    def __init__(self, cu_seqlens: torch.Tensor) -> None:
        self.offsets = [int(v) for v in cu_seqlens.tolist()]
        self.lengths = [hi - lo for lo, hi in zip(self.offsets[:-1], self.offsets[1:])]
        self.max_seqlen = max(self.lengths) if self.lengths else 0
        # Interior boundaries only — what tensor_split wants.
        self.split_bounds = self.offsets[1:-1]
        self._by_device = {cu_seqlens.device: cu_seqlens.to(torch.int32)}

    @property
    def blocks(self) -> int:
        return len(self.lengths)

    def cu_on(self, device: torch.device) -> torch.Tensor:
        cu = self._by_device.get(device)
        if cu is None:
            cu = torch.tensor(self.offsets, dtype=torch.int32, device=device)
            self._by_device[device] = cu
        return cu


def _as_geometry(cu: "torch.Tensor | VarlenGeometry") -> VarlenGeometry:
    return cu if isinstance(cu, VarlenGeometry) else VarlenGeometry(cu)


def _run_flash_varlen(q, k, v, geo_q: VarlenGeometry, geo_k: VarlenGeometry):
    flash_attn_varlen_func = _flash_varlen_func
    logger.debug(
        "seedvr2 varlen_attention: flash_attn_varlen_func kernel in use (blocks=%d)",
        geo_q.blocks,
    )
    return flash_attn_varlen_func(
        q, k, v,
        cu_seqlens_q=geo_q.cu_on(q.device),
        cu_seqlens_k=geo_k.cu_on(q.device),
        max_seqlen_q=geo_q.max_seqlen,
        max_seqlen_k=geo_k.max_seqlen,
        causal=False,
    )


def _varlen_attention_fallback(q, k, v, geo_q: VarlenGeometry, geo_k: VarlenGeometry):
    if _attention_backend is None:
        raise RuntimeError(
            "seedvr2 varlen_attention: no attention backend wired — call set_attention_backend() first"
        )
    q_blocks = torch.tensor_split(q, geo_q.split_bounds, dim=0)
    k_blocks = torch.tensor_split(k, geo_k.split_bounds, dim=0)
    v_blocks = torch.tensor_split(v, geo_k.split_bounds, dim=0)

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
    cu_seqlens_q: "torch.Tensor | VarlenGeometry",  # (nblocks + 1,) cumulative q lengths
    cu_seqlens_k: "torch.Tensor | VarlenGeometry",
) -> torch.Tensor:
    geo_q = _as_geometry(cu_seqlens_q)
    geo_k = _as_geometry(cu_seqlens_k)
    if _flash_varlen_available(q):
        try:
            return _run_flash_varlen(q, k, v, geo_q, geo_k)
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

    return _varlen_attention_fallback(q, k, v, geo_q, geo_k)
