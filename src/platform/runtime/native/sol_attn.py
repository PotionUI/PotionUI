"""Opt-in Sol-Attn sparse attention for the native engine.

Sol-Attn summarises every 128-token KV block by its mean vector, scores those
summaries against pooled query blocks, and computes exact attention only over
the blocks whose score clears a per-query-block threshold (plus the local
diagonal window, which is always exact). It is an **approximation**: the same
inputs produce a different — not merely differently-rounded — output than
:mod:`~src.platform.runtime.native.attention`'s dense backends. That is why
nothing here is ever reached unless a caller hands over a
:class:`SolAttnContext`, and why every preset that exposes it defaults to off.

The vendored implementation is ``vendor/sol_attn/`` (Apache-2.0). Two backends
live there; ``NATIVE_SOL_ATTN_BACKEND`` picks between them:

``flex`` (default)
    ``vendor/sol_attn/flex.py`` — routing in plain torch, execution through
    ``torch.nn.attention.flex_attention``. Needs only torch, and it is the only
    backend that honours ``sink_tokens``.
``kernel``
    ``vendor/sol_attn/interface.py`` — upstream's original kernels: CuTe DSL on
    SM90/SM100 (whose modules are not vendored, so it raises there) and the
    Triton reference on SM120. Upstream measures the Triton reference as
    *slower* than SDPA on long sequences, so it is not the default; it exists
    for A/B against the flex path.

**Failure contract.** :func:`sol_attention` never raises and never propagates a
backend failure. The first time anything goes wrong — no CUDA, an unsupported
dtype or head dim, a missing ``triton``, a torch too old for
``flex_attention``, a kernel that refuses this GPU, a compile error — it logs
ONE warning naming the reason and disables itself for the rest of the process.
Every later call returns ``None`` immediately and the caller silently uses its
normal dense attention. A user on the wrong hardware gets a slower generation
and one log line, never a crashed one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

import torch
from torch import Tensor

logger = logging.getLogger(__name__)

# Both backends hard-require this head dim (the kernels are written for it and
# the flex routing pools 128-wide blocks).
SOL_ATTN_HEAD_DIM = 128

# Below two full routing blocks there is nothing to route: the threshold's
# variance term is degenerate over a single KV block and every block is inside
# the always-exact diagonal window anyway. Skipped, not disabled — a short
# sequence is a property of the call, not of the machine.
_MIN_TOKENS = 256

_DEFAULT_BACKEND = "flex"

# --- transient-VRAM estimate -------------------------------------------------
#
# Every constant below is counted off `vendor/sol_attn/flex.py`'s own
# allocations, not guessed. `S` is one full-size bfloat16 QKV tensor,
# `T_pad * heads * head_dim * 2` bytes.
#
# Full-size copies alive at peak that the DENSE path would not have made:
#   1  `v.contiguous()` in `sol_attention` below -- `v` reaches an arch module's
#      attention as a chunk view of the fused qkv projection, and the dense path
#      passes `v.transpose(1, 2)`, another view. Sol-Attn needs it materialised.
#   3  `F.pad(q/k/v, ...)` in `sol_attn_flex`, whenever the sequence is not a
#      multiple of 128. A packed H3 sequence essentially never is, so this is
#      counted unconditionally.
#   3  `q_h`/`k_h`/`v_h`, the `permute(0, 2, 1, 3).contiguous()` triple. The
#      padded `qp`/`kp`/`vp` stay bound to the end of `sol_attn_flex`, so both
#      sets are live at once -- this is the peak.
#   1  `out.permute(0, 2, 1, 3).contiguous()` on the way back to BTHD.
# The kernel's own output is NOT counted: the dense path allocates one too.
#
# This is a WORST CASE. If SDPA's selected backend internally materialises its
# strided inputs, the dense path pays some of the same cost and the true
# marginal is lower. Over-reserving streams a few more DiT layers; under-
# reserving reproduces the OOM this estimate exists to prevent.
_SOL_ATTN_QKV_COPIES = 8

# Routing tensors, all shaped [B, heads, N, N] over N = T_pad/128 blocks, in
# bytes per element, summed over what `_build_routing` holds simultaneously:
#   score bf16 (2) + `exact` bool (1) + `selected` bool (1) + the `| sink_sel`
#   temporary (1) + `selected.to(int32)` temporary (4) + `both` int32 (4) +
#   `order` int64 from argsort (8) + the int64 gather result (8) + its int32
#   cast (4) = 33, rounded to 32.
# NOTE: `score` is [B, heads, N, N], NOT [B, heads, T, N] -- `_build_routing`
# pools the QUERIES into blocks too (`q_bar = _block_means(q_h, ...)`), so this
# whole family is quadratic in the BLOCK count, not in the token count. At
# T=43k it is ~0.2 GB, two orders of magnitude below the QKV copies.
_SOL_ATTN_ROUTING_BYTES_PER_BLOCK_PAIR = 32

# `kc` and `q_bar`, the [B, heads, N, head_dim] bf16 block means.
_SOL_ATTN_BLOCK_MEAN_TENSORS = 2

_SOL_ATTN_BF16_BYTES = 2
_SOL_ATTN_BLOCK = 128
_SOL_ATTN_MARGIN = 1.10
_BYTES_PER_GB = 1024 ** 3


def estimate_transient_gb(seq_len: int, heads: int, head_dim: int = SOL_ATTN_HEAD_DIM) -> float:
    """Peak extra VRAM one Sol-Attn call needs beyond the dense path, in GB.

    Callers hand this to ``place_dit_for_sequence``'s ``reserve_gb`` so the DiT
    placement leaves room for it. Without that, a card placed right at the
    activation-reserve ceiling OOMs partway into sampling on one of the
    full-size QKV copies (observed at 768x1344 / 141 frames: a 590 MiB
    allocation failing with 151 MiB free -- exactly one padded
    ``T_pad * 56 * 128 * 2``-byte copy).

    Returns 0.0 below the sequence length :func:`sol_attention` will actually
    route, so the estimate agrees with the runtime's own skip.
    """
    if seq_len < _MIN_TOKENS or heads <= 0 or head_dim <= 0:
        return 0.0
    padded = -(-seq_len // _SOL_ATTN_BLOCK) * _SOL_ATTN_BLOCK
    blocks = padded // _SOL_ATTN_BLOCK

    qkv_bytes = padded * heads * head_dim * _SOL_ATTN_BF16_BYTES * _SOL_ATTN_QKV_COPIES
    routing_bytes = heads * blocks * blocks * _SOL_ATTN_ROUTING_BYTES_PER_BLOCK_PAIR
    block_mean_bytes = (
        heads * blocks * head_dim * _SOL_ATTN_BF16_BYTES * _SOL_ATTN_BLOCK_MEAN_TENSORS
    )
    total = (qkv_bytes + routing_bytes + block_mean_bytes) * _SOL_ATTN_MARGIN
    return total / _BYTES_PER_GB


@dataclass
class SolAttnContext:
    """Per-generation Sol-Attn settings, threaded through an arch module's
    forward as an optional keyword.

    ``tau`` — sparsity temperature. Larger skips more KV blocks (faster, less
    accurate); ``1.0`` is upstream's default.

    ``sink_tokens`` — length of a sequence PREFIX whose KV blocks every query
    attends to exactly. For a packed-sequence model this is the conditioning
    (text / reference / audio rows) that must not be approximated. ``0``
    disables the sink.

    ``dense`` — set per sampling step by the caller. ``True`` means "run this
    step on the normal dense path", which is how the final steps of a
    trajectory stay exact (their sparse error is the most visible, since the
    noise that would mask it is gone).
    """

    tau: float = 1.0
    sink_tokens: int = 0
    dense: bool = False


_backend: Optional[Callable[..., Tensor]] = None
_disabled_reason: Optional[str] = None


def reset_sol_attn_state() -> None:
    """Forget the loaded backend and any disable latch (tests)."""
    global _backend, _disabled_reason
    _backend = None
    _disabled_reason = None


def sol_attn_disabled_reason() -> Optional[str]:
    """Why Sol-Attn turned itself off this process, or ``None`` if it has not."""
    return _disabled_reason


def _disable(reason: str) -> None:
    global _disabled_reason
    if _disabled_reason is not None:
        return
    _disabled_reason = reason
    logger.warning(
        "[SOL-ATTN] disabled for the rest of this process (%s) -- generation continues on the "
        "normal attention path, unchanged", reason,
    )


def _load_backend() -> Callable[..., Tensor]:
    choice = os.environ.get("NATIVE_SOL_ATTN_BACKEND", _DEFAULT_BACKEND).strip().lower() or _DEFAULT_BACKEND
    if choice == "flex":
        from vendor.sol_attn.flex import sol_attn_flex

        return sol_attn_flex
    if choice == "kernel":
        from vendor.sol_attn.interface import sol_attn

        return sol_attn
    raise ValueError(f"NATIVE_SOL_ATTN_BACKEND must be 'flex' or 'kernel', got {choice!r}")


def _unsupported(q: Tensor) -> Optional[str]:
    """A reason string when this machine/tensor can never run Sol-Attn."""
    if q.device.type != "cuda":
        return f"needs a CUDA device, got {q.device.type}"
    if q.dtype != torch.bfloat16:
        return f"needs bfloat16 activations, got {q.dtype}"
    if q.shape[-1] != SOL_ATTN_HEAD_DIM:
        return f"needs head_dim {SOL_ATTN_HEAD_DIM}, got {q.shape[-1]}"
    capability = torch.cuda.get_device_capability(q.device)
    if capability < (8, 0):
        return f"needs compute capability 8.0+, got {capability[0]}.{capability[1]}"
    return None


def sol_attention(q: Tensor, k: Tensor, v: Tensor, ctx: Optional[SolAttnContext]) -> Optional[Tensor]:
    """Sparse attention over BTHD ``(B, S, H, D)`` tensors, or ``None``.

    ``None`` means "not run" for ANY reason — no context, a dense-forced step,
    a sequence too short to route, an unsupported machine, or a backend
    failure. The caller must treat it as "use the normal attention path"; it is
    never an error condition.

    The tensors are made contiguous here rather than by the caller, so a caller
    that passes ``ctx=None`` performs no extra work at all and its output stays
    bit-identical to a build without this module.
    """
    if ctx is None or ctx.dense or _disabled_reason is not None:
        return None
    if q.shape[1] < _MIN_TOKENS:
        return None

    reason = _unsupported(q)
    if reason is not None:
        _disable(reason)
        return None

    global _backend
    try:
        backend = _backend or _load_backend()
        # The sink is only meaningful when it is a genuine prefix of a longer
        # sequence; a sink covering everything is just dense attention with
        # extra routing work.
        sink = int(ctx.sink_tokens) if 0 < ctx.sink_tokens < q.shape[1] else 0
        out = backend(
            q.contiguous(), k.contiguous(), v.contiguous(),
            tau=float(ctx.tau), sink_tokens=sink,
        )
    except Exception as exc:  # noqa: BLE001 - any backend failure means "fall back"
        _disable(f"{type(exc).__name__}: {exc}")
        return None

    if _backend is None:
        _backend = backend
        logger.info(
            "[SOL-ATTN] active: backend=%s tau=%.2f sink_tokens=%d seq_len=%d heads=%d",
            getattr(backend, "__module__", "?"), float(ctx.tau), sink, q.shape[1], q.shape[2],
        )
    return out


def build_sol_attn_context(
    *, enabled: Any, tau: Any, sink_tokens: int, log_prefix: str = "SOL-ATTN",
) -> Optional[SolAttnContext]:
    """Resolve flat preset knobs into a context, or ``None`` when off.

    A non-numeric ``tau`` falls back to the default rather than failing the
    generation — the same forgiveness ``build_step_cache`` gives its knobs.
    """
    if not bool(enabled):
        return None
    try:
        tau_value = float(tau)
    except (TypeError, ValueError):
        logger.warning("[%s] ignoring non-numeric tau=%r", log_prefix, tau)
        tau_value = 1.0
    return SolAttnContext(tau=tau_value, sink_tokens=int(sink_tokens))
