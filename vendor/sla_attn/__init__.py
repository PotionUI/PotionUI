# Vendored from PlagueKind/ComfyUI-PlagueKind-Nodes --
# https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes
# Source: `ComfyUI-H3-SLA-Attention/sla/{block_map,kernel}.py` at commit
# 6ca3037bd16dc143b6d461c67c87a28ca8074063 (2026-08-20).
# License: MIT (PlagueKind's reduction/adaptation layer) over Apache-2.0 (the
# algorithm itself, vendored a further hop upstream from LightX2V) -- see
# LICENSE in this directory for both texts and each file's own header for the
# per-file provenance.
# Local modifications: `patch.py` (ComfyUI model-patch glue) is NOT vendored;
# `src/platform/runtime/native/sla_attn.py` is its replacement seam. Otherwise
# runtime logic is unchanged -- see each file's header.

"""SLA -- block-sparse attention for MiniMax-H3.

Mean-pool Q into blocks, mean-pool a smoothed K into blocks, score the two
against each other with one small matmul, and keep only the top-k fraction of
key blocks per query block (:func:`get_block_map`), then run exact attention
over just those blocks (:func:`block_sparse_attention`). Nothing here is
trained and nothing is loaded: the published SLA LoRA adapts the *model* to
tolerate the resulting sparsity, it does not parameterise this step.

Needs ``triton`` at import time (:func:`mean_pool` and the attention kernel are
both Triton kernels), so this package is imported lazily by its one consumer,
:mod:`src.platform.runtime.native.sla_attn`, inside a try/except.
"""

from __future__ import annotations

from .block_map import get_block_map, mean_pool
from .kernel import block_sparse_attention

__all__ = ["get_block_map", "mean_pool", "block_sparse_attention"]
