"""Opt-in SLA sparse attention for the native engine, for MiniMax-H3.

SLA (Sparse Linear Attention, as published alongside the LightX2V SLA turbo
LoRA) mean-pools every KV block and every query block, scores the pooled pairs
with one small matmul, and keeps only the top-``k`` fraction of key blocks per
query block — exact attention over just those blocks, nothing else. Like
Sol-Attn it is an **approximation**: the same inputs produce a different — not
merely differently-rounded — output than
:mod:`~src.platform.runtime.native.attention`'s dense backends. That is why
nothing here is ever reached unless a caller hands over an
:class:`SlaAttnContext`, and why every preset that exposes it defaults to off.

The vendored implementation is ``vendor/sla_attn/`` (Apache-2.0 over MIT — see
its ``LICENSE``). Unlike Sol-Attn there is only one backend: a Triton kernel,
ported from LightX2V by way of a ComfyUI node this module's seam replaces (see
``vendor/sla_attn/__init__.py`` for exactly what was and was not carried
over). ``triton`` is imported lazily, inside the same try/except that catches
every other failure mode, so a machine without a usable triton pays nothing
for this module existing.

**Failure contract.** :func:`sla_attention` never raises and never propagates a
backend failure. The first time anything goes wrong — no CUDA, an unsupported
dtype or head dim, a missing ``triton``, a kernel that refuses this GPU or
exhausts every launch config in its ladder — it logs ONE warning naming the
reason and disables itself for the rest of the process. Every later call
returns ``None`` immediately and the caller silently uses its normal dense
attention. A user on the wrong hardware gets a slower generation and one log
line, never a crashed one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import torch
from torch import Tensor

logger = logging.getLogger(__name__)

# The kernel is written for this head dim; the routing pools D-wide vectors.
SLA_ATTN_HEAD_DIM = 128

# Below this, block sparsity costs more than it saves: upstream's own measured
# break-even is ~0.60 sparsity, and a short sequence has too few blocks for
# top-k selection to skip enough work to clear that bar. Skipped, not
# disabled — a short sequence is a property of the call, not the machine.
_MIN_TOKENS = 8192

# Requested sparsity above this trades negligible extra speed for a routing
# so aggressive it starts dropping blocks a real generation needs; upstream's
# own UI caps the knob here for the same reason.
_MAX_SPARSITY = 0.95

_VALID_BLOCK_SIZES = (64, 128)
_DEFAULT_BLOCK_SIZE = 64
_DEFAULT_SPARSITY = 0.90

# --- transient-VRAM estimate -------------------------------------------------
#
# Every constant below is counted off `vendor/sla_attn/{block_map,kernel}.py`'s
# own allocations, not guessed. Unlike Sol-Attn's threshold routing, SLA's
# lookup table is sized by `topk`, which depends on the sparsity a caller
# chooses — a knob this function does not take (it mirrors Sol-Attn's
# `estimate_transient_gb` signature so a placement call site does not need to
# know the value yet). The lookup-table term below therefore assumes this
# module's own default sparsity (0.90 -> topk_ratio 0.10); a caller running at
# a much lower sparsity gets a slight under-reserve on that one (small) term,
# never on the dominant one.
#
# Full-size copies alive at peak that the DENSE path would not have made:
#   1  `v.contiguous()` in `sla_attention` below -- `v` reaches an arch
#      module's attention as a chunk view of the fused qkv projection; q and k
#      are already contiguous (RMSNorm + rotary output), so their
#      `.contiguous()` calls are no-ops and cost nothing. This is the same
#      single real copy Sol-Attn's estimate counts, and it dominates here too.
_SLA_ATTN_QKV_COPIES = 1

# `pooled_q` and `pooled_k`, the `(heads, blocks, head_dim)` fp32 block means
# `get_block_map` computes via `mean_pool`. Two tensors, not more: the
# unshifted `mean_pool(k, BLKK)` result is superseded in place by the
# smooth-k subtraction and does not add a second live copy at steady state.
_SLA_ATTN_POOLED_TENSORS = 2

# `pooled_score`, `(heads, blocks, blocks)` fp32 -- the one pooled-Q-by-pooled-K
# matmul `get_block_map` computes before top-k. Quadratic in the BLOCK count,
# not the token count, same as Sol-Attn's routing term.
_SLA_ATTN_SCORE_BYTES_PER_ELEMENT = 4

# `torch.topk`'s `.indices` (int64) plus `get_block_map`'s own
# `.to(torch.int32)` copy of it (the `lut` the kernel actually reads) --
# both `(heads, blocks, topk)`, both live at once because the cast does not
# free its source until the caller's reference to `lut` (the topk indices
# variable) goes out of scope, which is after the cast.
_SLA_ATTN_LUT_INT64_BYTES = 8
_SLA_ATTN_LUT_INT32_BYTES = 4

# Worst case: BLKK=64 (SLA_ATTN's forced choice whenever block_size=128) packs
# more, smaller blocks than 128 into the same sequence, so it is the more
# conservative choice for every block-count-dependent term above.
_SLA_ATTN_RESERVE_BLOCK = 64
_SLA_ATTN_DEFAULT_TOPK_RATIO = 1.0 - _DEFAULT_SPARSITY

_SLA_ATTN_BF16_BYTES = 2
_SLA_ATTN_FP32_BYTES = 4
_SLA_ATTN_MARGIN = 1.10
_BYTES_PER_GB = 1024 ** 3


def estimate_transient_gb(seq_len: int, heads: int, head_dim: int = SLA_ATTN_HEAD_DIM) -> float:
    """Peak extra VRAM one SLA-Attn call needs beyond the dense path, in GB.

    Callers hand this to ``place_dit_for_sequence``'s ``reserve_gb`` the same
    way :func:`~src.platform.runtime.native.sol_attn.estimate_transient_gb` is
    used, so DiT placement leaves room for it.

    Returns 0.0 below the sequence length :func:`sla_attention` will actually
    route, so the estimate agrees with the runtime's own skip.
    """
    if seq_len < _MIN_TOKENS or heads <= 0 or head_dim <= 0:
        return 0.0
    padded = -(-seq_len // _SLA_ATTN_RESERVE_BLOCK) * _SLA_ATTN_RESERVE_BLOCK
    blocks = padded // _SLA_ATTN_RESERVE_BLOCK

    qkv_bytes = padded * heads * head_dim * _SLA_ATTN_BF16_BYTES * _SLA_ATTN_QKV_COPIES
    pooled_bytes = _SLA_ATTN_POOLED_TENSORS * heads * blocks * head_dim * _SLA_ATTN_FP32_BYTES
    score_bytes = heads * blocks * blocks * _SLA_ATTN_SCORE_BYTES_PER_ELEMENT

    topk = max(1, min(blocks, int(_SLA_ATTN_DEFAULT_TOPK_RATIO * blocks)))
    lut_bytes = heads * blocks * topk * (_SLA_ATTN_LUT_INT64_BYTES + _SLA_ATTN_LUT_INT32_BYTES)

    total = (qkv_bytes + pooled_bytes + score_bytes + lut_bytes) * _SLA_ATTN_MARGIN
    return total / _BYTES_PER_GB


@dataclass
class SlaAttnContext:
    """Per-generation SLA-Attn settings, threaded through an arch module's
    forward as an optional keyword.

    ``sparsity`` — fraction of key blocks skipped; top-k keeps ``1 - sparsity``
    of them. Clamped to ``0.95``. ``0.0`` means nothing to skip, so
    :func:`sla_attention` returns ``None`` rather than paying routing overhead
    for an exact result dense attention already gives for less.

    ``block_size`` — the query block width (``BLKQ``), ``64`` or ``128``.
    ``BLKK`` (the key block width) is forced to ``64`` whenever ``block_size``
    is ``128``: on sm_120 a 128x128 tile needs more shared memory than the
    device has and cannot launch at all.

    ``prefix_tokens`` — length of a packed-sequence PREFIX pinned into every
    query's block selection, on top of its top-k budget rather than displacing
    it. For MiniMax-H3 that is the ``[text | cond | audio]`` prefix top-k
    alone tends to starve. ``0`` disables the pin, and a value covering the
    whole sequence is treated as ``0`` — a pin covering everything is dense
    attention with extra routing work.

    ``dense`` — set per sampling step by the caller. ``True`` means "run this
    step on the normal dense path", the same trailing-exact-steps idiom
    Sol-Attn's context uses.
    """

    sparsity: float = _DEFAULT_SPARSITY
    block_size: int = _DEFAULT_BLOCK_SIZE
    prefix_tokens: int = 0
    dense: bool = False


_disabled_reason: Optional[str] = None
_active_logged = False


def reset_sla_attn_state() -> None:
    """Forget the disable latch and the "active" log-once flag (tests)."""
    global _disabled_reason, _active_logged
    _disabled_reason = None
    _active_logged = False


def sla_attn_disabled_reason() -> Optional[str]:
    """Why SLA-Attn turned itself off this process, or ``None`` if it has not."""
    return _disabled_reason


def _disable(reason: str) -> None:
    global _disabled_reason
    if _disabled_reason is not None:
        return
    _disabled_reason = reason
    logger.warning(
        "[SLA-ATTN] disabled for the rest of this process (%s) -- generation continues on the "
        "normal attention path, unchanged", reason,
    )


def _unsupported(q: Tensor) -> Optional[str]:
    """A reason string when this machine/tensor can never run SLA-Attn."""
    if q.device.type != "cuda":
        return f"needs a CUDA device, got {q.device.type}"
    if q.dtype not in (torch.bfloat16, torch.float16):
        return f"needs bfloat16 or float16 activations, got {q.dtype}"
    if q.shape[-1] != SLA_ATTN_HEAD_DIM:
        return f"needs head_dim {SLA_ATTN_HEAD_DIM}, got {q.shape[-1]}"
    capability = torch.cuda.get_device_capability(q.device)
    if capability < (8, 0):
        return f"needs compute capability 8.0+, got {capability[0]}.{capability[1]}"
    return None


def sla_attention(q: Tensor, k: Tensor, v: Tensor, ctx: Optional[SlaAttnContext]) -> Optional[Tensor]:
    """Block-sparse attention over BTHD ``(B, S, H, D)`` tensors, or ``None``.

    ``None`` means "not run" for ANY reason — no context, a dense-forced step,
    a sequence too short to route, zero sparsity, an unsupported machine, or a
    backend failure. The caller must treat it as "use the normal attention
    path"; it is never an error condition.

    The tensors are made contiguous here rather than by the caller, so a
    caller that passes ``ctx=None`` performs no extra work at all and its
    output stays bit-identical to a build without this module.
    """
    if ctx is None or ctx.dense or _disabled_reason is not None:
        return None
    if q.shape[1] < _MIN_TOKENS:
        return None
    if ctx.sparsity <= 0.0:
        return None

    reason = _unsupported(q)
    if reason is not None:
        _disable(reason)
        return None

    seq_len = q.shape[1]
    sparsity = min(float(ctx.sparsity), _MAX_SPARSITY)
    topk_ratio = 1.0 - sparsity
    blkq = ctx.block_size if ctx.block_size in _VALID_BLOCK_SIZES else _DEFAULT_BLOCK_SIZE
    blkk = 64 if blkq == 128 else blkq
    # A pin covering the whole sequence is dense attention with extra routing
    # work, the same reasoning Sol-Attn applies to its sink.
    prefix = int(ctx.prefix_tokens) if 0 < ctx.prefix_tokens < seq_len else 0

    try:
        from vendor.sla_attn import block_sparse_attention, get_block_map

        qc, kc, vc = q.contiguous(), k.contiguous(), v.contiguous()
        lut, topk = get_block_map(qc, kc, topk_ratio, blkq, blkk, protect_upto=prefix)
        out = block_sparse_attention(qc, kc, vc, lut, topk, blkq, blkk)
    except Exception as exc:  # noqa: BLE001 - any backend failure means "fall back"
        _disable(f"{type(exc).__name__}: {exc}")
        return None

    global _active_logged
    if not _active_logged:
        _active_logged = True
        blocks = -(-seq_len // blkk)
        logger.info(
            "[SLA-ATTN] active: sparsity=%.2f block=%dx%d kept=%d/%d prefix=%d seq_len=%d",
            sparsity, blkq, blkk, topk, blocks, prefix, seq_len,
        )
    return out


def build_sla_attn_context(
    *, enabled: Any, sparsity: Any, block_size: Any, prefix_tokens: int, log_prefix: str = "SLA-ATTN",
) -> Optional[SlaAttnContext]:
    """Resolve flat preset knobs into a context, or ``None`` when off.

    Non-numeric knobs fall back to the default rather than failing the
    generation — the same forgiveness ``build_sol_attn_context`` gives its
    knobs. A ``block_size`` outside ``(64, 128)`` also falls back, since the
    kernel's launch-config ladder only covers those two.
    """
    if not bool(enabled):
        return None
    try:
        sparsity_value = float(sparsity)
    except (TypeError, ValueError):
        logger.warning("[%s] ignoring non-numeric sparsity=%r", log_prefix, sparsity)
        sparsity_value = _DEFAULT_SPARSITY

    try:
        block_size_value = int(block_size)
    except (TypeError, ValueError):
        logger.warning("[%s] ignoring non-numeric block_size=%r, falling back to %d",
                        log_prefix, block_size, _DEFAULT_BLOCK_SIZE)
        block_size_value = _DEFAULT_BLOCK_SIZE
    if block_size_value not in _VALID_BLOCK_SIZES:
        logger.warning("[%s] ignoring unsupported block_size=%r, falling back to %d",
                        log_prefix, block_size, _DEFAULT_BLOCK_SIZE)
        block_size_value = _DEFAULT_BLOCK_SIZE

    try:
        prefix_value = int(prefix_tokens)
    except (TypeError, ValueError):
        logger.warning("[%s] ignoring non-numeric prefix_tokens=%r", log_prefix, prefix_tokens)
        prefix_value = 0

    return SlaAttnContext(sparsity=sparsity_value, block_size=block_size_value, prefix_tokens=prefix_value)
