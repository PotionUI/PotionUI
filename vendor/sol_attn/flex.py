# Vendored from ComfyUI_sol-attn_Blackwell —
# https://github.com/KingGore/ComfyUI_sol-attn_Blackwell
# Source file: sol_attn_flex.py (repository root) at commit
# a8a9584e1ed700f2ce3b7569048cab0071bbf58a.
# License: Apache-2.0 (see LICENSE in this directory).
# Local modifications: the debug-log switch's environment variable is
# `NATIVE_SOL_ATTN_DEBUG` rather than `COMFYUI_SOL_ATTN_DEBUG`, matching this
# engine's other `NATIVE_*` switches; the three comments that were written in
# Chinese are translated. No executable change — the routing math, the sink
# handling, the block-mask construction and the SDPA fallback are upstream's.
#
# This is the backend `src/platform/runtime/native/sol_attn.py` actually runs.
# The `interface.py` kernel path beside it is upstream's original and is kept
# whole, but its SM120 branch (`triton_ref`) drops `sink_tokens` on the floor
# and upstream measures it as slower than plain SDPA on long sequences — the
# exact regime MiniMax-H3 lives in — so it is not the default here.

"""Sol-Attn for SM120 (RTX 5090) via torch flex_attention.

Sol-Attn's routing (which KV blocks are "important") is computed cheaply at
128-token granularity, then executed by ``torch.nn.attention.flex_attention``
whose ``torch.compile``-fused backend lowers to FlashAttention-3-style
kernels natively optimized for Blackwell consumer (SM120).

This replaces the old ``triton_ref`` SM120 backend, which ran a serial
per-block loop that was far slower than SDPA on long sequences.

Why pure-torch routing (no ``sol_attn.preprocess``):
  * The preprocess Triton kernels autotune-*benchmark* their configs on first
    call, allocating a large scratch buffer.  With ComfyUI already holding
    ~30 GB of the 32 GB VRAM, that allocation OOMs before the kernel even
    runs.  Computing the block means in torch needs no scratch and no tuning.
  * ``flex_attention`` needs 128-token blocks (a compiled-kernel
    requirement), but ``preprocess`` computes 64-token summaries.  Computing
    the routing directly at 128 tokens avoids a granularity mismatch.

Input/output format: BTHD ``[B, T, H, D]`` (the Sol-Attn convention).
"""

from __future__ import annotations

import logging
import math

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

# Diagnostic logging. NATIVE_SOL_ATTN_DEBUG=1 prints each call's sequence
# length, free VRAM and sparse density. Off by default — one line per attention
# call per block per step would flood the log.
import os as _os
_DEBUG = _os.environ.get("NATIVE_SOL_ATTN_DEBUG", "0") == "1"

# flex_attention compiled-kernel block size. 128 is the minimum the kernel
# accepts (BLOCK_M/BLOCK_N are 128).
FLEX_BLOCK = 128
_LN2 = 1.4426950408889634


# ---------------------------------------------------------------------------
# Compiled flex_attention kernel (cached per process).
# ---------------------------------------------------------------------------
_compiled_flex = None


def _get_compiled_flex():
    global _compiled_flex
    if _compiled_flex is None:
        from torch.nn.attention.flex_attention import flex_attention
        _compiled_flex = torch.compile(flex_attention)
    return _compiled_flex


# ---------------------------------------------------------------------------
# Routing (Sol-Attn's "which KV blocks are important").
# ---------------------------------------------------------------------------
def _block_means(x_h: torch.Tensor, n_blocks: int, T: int):
    """Mean of each 128-token block along the sequence dim.

    x_h: [B, H, T_pad, D] (already contiguous, padded to T_pad).
    Returns [B, H, n_blocks, D].
    """
    B, H, T_pad, D = x_h.shape
    return x_h.view(B, H, n_blocks, FLEX_BLOCK, D).mean(dim=3)


def _build_routing(
    q_h: torch.Tensor,  # [B, H, T_pad, D] padded query
    k_h: torch.Tensor,  # [B, H, T_pad, D] padded key
    T: int,             # real sequence length
    scale: float,
    tau: float,
    device: torch.device,
    sink_tokens: int = 0,     # exact-KV sink region (text/cond/audio rows) in tokens
    dense_sink_rows: bool = False,  # also run sink query rows dense (exact_kv_and_rows)
):
    """Return (kv_num_blocks, kv_indices, mask_mod) for the Sol-Attn mask.

    Faithful to the Sol-Attn diag threshold: a KV block is kept if its pilot
    score ``q_bar @ kc`` exceeds ``mean + tau * std`` of the query-block
    score distribution, plus the local diagonal window (always exact).

    ``sink_tokens`` (exact_kv): the first ``sink_tokens`` rows are an exact
    KV sink — every query sees their KV blocks exactly. This is the packed
    text/cond/reference rows the model must attend to faithfully.
    ``dense_sink_rows`` (exact_kv_and_rows): additionally runs those sink
    query rows fully dense (they see every KV block), so a generated stream
    living inside the sink region (e.g. the H3 audio rows) is exact.
    """
    B, H, T_pad, D = q_h.shape
    n_blocks = T_pad // FLEX_BLOCK

    # 128-token block means.
    kc = _block_means(k_h, n_blocks, T)  # [B, H, N, D]
    q_bar = _block_means(q_h, n_blocks, T)  # [B, H, N, D]

    # Sol-Attn diag threshold (scale-invariant; the ln2 factor cancels).
    kc_mean = kc.mean(dim=2, keepdim=True)  # [B, H, 1, D]
    kc_var = kc.var(dim=2, unbiased=False, keepdim=True)  # [B, H, 1, D]
    mean_term = (q_bar @ kc_mean.transpose(-1, -2)).squeeze(-1)  # [B, H, N]
    var_term = ((q_bar * q_bar) @ kc_var.transpose(-1, -2)).squeeze(-1)  # [B, H, N]
    threshold = mean_term + tau * torch.sqrt(torch.clamp(var_term, min=0.0) + 1e-6)

    # pilot score [B, H, N, N]
    score = torch.einsum("bhnd,bhmd->bhnm", q_bar, kc)
    exact = score > threshold.unsqueeze(-1)

    block_idx = torch.arange(n_blocks, device=device)
    qb = block_idx.view(1, 1, n_blocks, 1)
    kb = block_idx.view(1, 1, 1, n_blocks)
    diag = (qb - kb).abs() <= 1  # local window always exact
    selected = exact | diag

    # exact-KV sink: the sink KV blocks are always selected for every query.
    # The sink is a prefix of the sequence, so round UP to the block boundary
    # (the flex kernel is 128-token block granular) to cover the whole region.
    sink_blocks = (min(sink_tokens, T) + FLEX_BLOCK - 1) // FLEX_BLOCK
    if sink_blocks > 0:
        sink_sel = kb < sink_blocks  # [1, 1, 1, N]
        selected = selected | sink_sel
        if dense_sink_rows:
            # dense sink query rows: those rows see every KV block.
            q_sink = (qb < sink_blocks)  # [1, 1, N, 1]
            selected = selected | q_sink

    kv_num_blocks = selected.sum(dim=-1).to(torch.int32)  # [B, H, N]
    # Space-fill to the FULL N columns (like create_block_mask).  The kernel
    # derives the KV dimension from the last dim of the indices, so it MUST be
    # N (the number of 128-token blocks), not the max row count.  Truncating to
    # max_b makes the transposed q_indices the wrong size and the kernel reads
    # out of bounds.
    kvar = block_idx.view(1, 1, 1, n_blocks)
    both = selected.to(torch.int32) * kvar
    order = torch.argsort(both, dim=-1, descending=True)  # selected first, then 0s
    indices = torch.gather(kvar.expand_as(both), dim=-1, index=order)
    indices = indices.to(torch.int32)  # int32 required by the kernel

    def boundary_mask_mod(b, h, q_idx, kv_idx):
        return (q_idx < T) & (kv_idx < T)

    return kv_num_blocks, indices, boundary_mask_mod


def _center_block_mask(kv_num_blocks, kv_indices, T, mask_mod):
    from torch.nn.attention.flex_attention import BlockMask

    return BlockMask.from_kv_blocks(
        kv_num_blocks, kv_indices,
        BLOCK_SIZE=FLEX_BLOCK,
        mask_mod=mask_mod,
        seq_lengths=(T, T),
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def sol_attn_flex(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    *,
    scale: float | None = None,
    tau: float = 1.0,
    thresh_type: str = "diag",
    sink_tokens: int = 0,
    dense_sink_rows: bool = False,
) -> torch.Tensor:
    """Compute noncausal Sol-Attn for contiguous BF16 BTHD tensors on SM120.

    Args:
        q, k, v: BTHD ``[B, T, H, D]`` bfloat16 contiguous tensors.
        scale: attention scale; defaults to ``1/sqrt(D)``.
        tau: sparsification temperature (larger -> more KV blocks skipped).
        thresh_type: accepted for API compatibility; routing uses the diag
            threshold (the ``"exact"`` full-covariance variant adds negligible
            routing benefit and extra cost here).
        sink_tokens: exact-KV sink region (first ``sink_tokens`` rows) that
            every query attends to exactly. 0 = disabled.
        dense_sink_rows: also run the sink query rows fully dense.

    Returns:
        The attention output in BTHD format, same shape as ``q``.
    """
    if q.ndim != 4 or q.shape != k.shape or q.shape != v.shape:
        raise ValueError("q, k, v must share shape [B, T, H, 128]")
    if q.shape[-1] != 128:
        raise ValueError("Sol-Attn requires head dimension 128")
    if q.dtype != torch.bfloat16:
        raise TypeError("Sol-Attn requires bfloat16 inputs")
    if q.device.type != "cuda":
        raise ValueError("Sol-Attn requires CUDA tensors")

    batch, T, H, D = q.shape
    scale = D ** -0.5 if scale is None else float(scale)
    tau = float(tau)
    sink_tokens = int(sink_tokens)
    if sink_tokens < 0 or sink_tokens > T:
        raise ValueError(f"sink_tokens must be in [0, T], got {sink_tokens}")
    dense_sink_rows = bool(dense_sink_rows)

    if T == 0:
        return q.clone()

    device = q.device
    # --- diagnostics (off unless NATIVE_SOL_ATTN_DEBUG=1) ---
    if _DEBUG:
        _free_mb = torch.cuda.mem_get_info(device)[0] / (1024 ** 2)
        logger.info(
            f"[SolAttnFlex] call q={tuple(q.shape)} | free_vram={_free_mb:.0f}MB | "
            f"tau={tau} | thresh={thresh_type}"
        )

    # Pad to a multiple of FLEX_BLOCK so the compiled kernel is happy.
    T_pad = ((T + FLEX_BLOCK - 1) // FLEX_BLOCK) * FLEX_BLOCK
    if T_pad != T:
        qp = F.pad(q, (0, 0, 0, 0, 0, T_pad - T))
        kp = F.pad(k, (0, 0, 0, 0, 0, T_pad - T))
        vp = F.pad(v, (0, 0, 0, 0, 0, T_pad - T))
    else:
        qp, kp, vp = q, k, v

    q_h = qp.permute(0, 2, 1, 3).contiguous()  # [B, H, T_pad, D]
    k_h = kp.permute(0, 2, 1, 3).contiguous()
    v_h = vp.permute(0, 2, 1, 3).contiguous()

    kv_num_blocks, indices, mask_mod = _build_routing(
        q_h, k_h, T, scale, tau, device,
        sink_tokens=sink_tokens, dense_sink_rows=dense_sink_rows,
    )
    block_mask = _center_block_mask(kv_num_blocks, indices, T_pad, mask_mod)

    # --- diagnostics (off unless NATIVE_SOL_ATTN_DEBUG=1) ---
    if _DEBUG:
        _density = kv_num_blocks.float().mean().item() / (T_pad // FLEX_BLOCK)
        logger.info(
            f"[SolAttnFlex] density={_density:.3f} (skip {100*(1-_density):.1f}% KV blocks) | "
            f"n_blocks={T_pad//FLEX_BLOCK} | pad={T_pad-T}"
        )

    try:
        out = _get_compiled_flex()(q_h, k_h, v_h, block_mask=block_mask, scale=scale)
    except Exception as exc:
        logger.warning(
            f"[SolAttnFlex] compiled flex_attention failed "
            f"({exc.__class__.__name__}: {exc}); falling back to SDPA"
        )
        out = F.scaled_dot_product_attention(q_h, k_h, v_h, scale=scale)

    out = out.permute(0, 2, 1, 3).contiguous()  # back to BTHD
    return out[:, :T, :, :]


def warmup(device: torch.device = None) -> None:
    """Compile the flex_attention kernel once (e.g. at plugin startup).

    Avoids the first-call compile latency during actual generation.
    """
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        return
    try:
        B, H, T, D = 1, 1, 256, 128
        q = torch.randn(B, H, T, D, device=device, dtype=torch.bfloat16)
        k = torch.randn(B, H, T, D, device=device, dtype=torch.bfloat16)
        v = torch.randn(B, H, T, D, device=device, dtype=torch.bfloat16)
        from torch.nn.attention.flex_attention import create_block_mask

        def noop(b, h, qi, kv):
            return torch.ones_like(qi, dtype=torch.bool)

        bm = create_block_mask(noop, B, H, T, T, device=device, BLOCK_SIZE=FLEX_BLOCK)
        _get_compiled_flex()(q, k, v, block_mask=bm)
        torch.cuda.synchronize(device)
        logger.info("[SolAttnFlex] flex_attention kernel compiled (warmup done)")
    except Exception as exc:
        logger.warning(f"[SolAttnFlex] warmup failed: {exc.__class__.__name__}: {exc}")


__all__ = ["sol_attn_flex", "warmup"]