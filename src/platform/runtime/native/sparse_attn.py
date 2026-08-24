"""The single seam arch modules thread a sparse-attention context through.

Two sparse-attention backends live behind :func:`sparse_attention` —
:class:`~src.platform.runtime.native.sol_attn.SolAttnContext` (threshold-based
KV-block routing, dispatched to :func:`~src.platform.runtime.native.sol_attn.sol_attention`)
and :class:`~src.platform.runtime.native.sla_attn.SlaAttnContext` (top-k
KV-block routing, dispatched to
:func:`~src.platform.runtime.native.sla_attn.sla_attention`) — each with its
own opt-in preset toggle, its own never-raises failure contract, and its own
process-lifetime disable latch, entirely independent of the other. An arch
module calls :func:`sparse_attention` with whichever context a preset built
(or ``None``) and never needs to know which backend, if either, actually ran.
"""

from __future__ import annotations

from typing import Optional, Union

from torch import Tensor

from src.platform.runtime.native.sla_attn import SlaAttnContext, sla_attention
from src.platform.runtime.native.sol_attn import SolAttnContext, sol_attention

__all__ = ["sparse_attention", "SolAttnContext", "SlaAttnContext"]

SparseAttnContext = Union[SolAttnContext, SlaAttnContext]


def sparse_attention(q: Tensor, k: Tensor, v: Tensor, ctx: Optional[SparseAttnContext]) -> Optional[Tensor]:
    """Route ``ctx`` to the backend it belongs to, or ``None`` for no context.

    ``None`` propagates from either backend exactly as it does from calling
    that backend directly — no context, a dense-forced step, an unsupported
    machine, a backend failure. The caller's fallback is unchanged either way.
    """
    if ctx is None:
        return None
    if isinstance(ctx, SolAttnContext):
        return sol_attention(q, k, v, ctx)
    if isinstance(ctx, SlaAttnContext):
        return sla_attention(q, k, v, ctx)
    raise TypeError(f"unknown sparse-attention context type: {type(ctx).__name__}")
