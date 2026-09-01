# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/attention/full_attn.py
"""Scaled dot-product attention over sparse (varlen-packed) tensors.

Only the sdpa branch is ported (the vendored fork's own downstream addition —
none of upstream's flash_attn/flash_attn_3/xformers branches apply here: this
project has no dependency on those packages and the native engine's own
attention dispatcher already picks the fastest kernel available). The packed
layout holds several sequences back to back; each is sliced out via
``layout`` and run separately through
``src.platform.runtime.native.attention.attention`` (its ``(B, H, L, D)``
contract is exactly what a ``[1, H, L, D]`` per-sequence slice already is), so
this gets the dispatcher's backend selection (sage/flash/sdpa) for free
instead of hardcoding ``F.scaled_dot_product_attention``. At batch=1 the loop
runs once, i.e. collapses to that single unmasked call.
"""

from __future__ import annotations

import torch

from .. import attention as native_attention
from .basic import SparseTensor

__all__ = ["sparse_scaled_dot_product_attention"]


def sparse_scaled_dot_product_attention(q: SparseTensor, k: SparseTensor, v: SparseTensor) -> SparseTensor:
    """
    Args:
        q (SparseTensor): [N, *, H, Ci] sparse tensor of queries.
        k (SparseTensor): [N, *, H, Ci] sparse tensor of keys.
        v (SparseTensor): [N, *, H, Co] sparse tensor of values.

    ``k`` and ``v`` are assumed to share the same coordinate map (upstream's
    own contract). Returns a SparseTensor with q's coordinate map.
    """
    assert q.shape[0] == k.shape[0] == v.shape[0], (
        f"batch size mismatch: q={q.shape[0]} k={k.shape[0]} v={v.shape[0]}"
    )
    assert k.layout == v.layout, "k and v must share the same coordinate map"

    q_layout, kv_layout = q.layout, k.layout
    q_feats, k_feats, v_feats = q.feats, k.feats, v.feats

    chunks = []
    for q_slice, kv_slice in zip(q_layout, kv_layout):
        # [L, H, C] -> [1, H, L, C], the layout attention() expects.
        q_i = q_feats[q_slice].transpose(0, 1).unsqueeze(0)
        k_i = k_feats[kv_slice].transpose(0, 1).unsqueeze(0)
        v_i = v_feats[kv_slice].transpose(0, 1).unsqueeze(0)
        o_i = native_attention.attention(q_i, k_i, v_i)
        chunks.append(o_i.squeeze(0).transpose(0, 1))

    out = torch.cat(chunks, dim=0)
    return q.replace(out)
