# Derived from: diffusers `src/diffusers/models/transformers/transformer_minimax_h3.py`
# (Apache-2.0, "Copyright 2025 The MiniMax Team and The HuggingFace Team") for the
# full-checkpoint architecture (packed-sequence forward, MM-RoPE, block/AdaLN/
# SwiGLU/token-refiner structure, mixed-precision contract). Diffusers has no
# pruned-checkpoint path at all (see the module docstring below); the pruned
# AdaLN-curve lookup was independently re-derived by reading ComfyUI's
# (GPL-3.0) MiniMax-H3 implementation for its *semantics* only — grid size,
# clamp-then-lerp, and the no-SiLU rule — and re-expressed here in this
# module's own code, not copied. Module names target the Comfy-Org
# single-file repack layout (`blocks.*`, `video_patch_proj`, `condition_proj`,
# `time_embedder.proj_in/proj_out`, ...), which differs from diffusers' own
# names (`transformer_blocks.*`, `proj_in`, `context_embedder`,
# `time_embedder.linear_1/linear_2`, ...) — this file is a structural port,
# not a name-for-name one.

"""MiniMax-H3 packed-sequence DiT — ``MiniMaxH3Model`` (``NativeArchModule``).

One 50-block dense transformer runs over **one packed 1-D sequence** holding
text rows, conditioning-media rows, audio rows and target video rows. There is
no cross-attention anywhere and no per-modality block weights — modality only
shows up in the two input patch projections (`video_patch_proj`,
`audio_patch_proj`), the per-row AdaLN modality tag, and the two output heads
(`final_layer.video_out`, `final_layer.audio_out`). The caller builds the
packed layout (row order, `(t, h, w)` rotary positions, per-row timestep index,
per-row modality tag) — this module never computes positions.

Two checkpoint shapes, one class (``MiniMaxH3Config.pruned`` selects the
branch; both share every other module):

  * **full** — a `time_embedder` sinusoidal-MLP produces one `time_embed_dim`
    (2688) vector per distinct timestep; every block's `adaln_proj.linear`
    projects it (after SiLU, at the embedder's fp32 precision, THEN cast — see
    ``MiniMaxH3AdalnProj``) to the six modulation tensors.
  * **pruned** — `time_embedder` is entirely absent (it is ~13B of the model's
    33B params, all AdaLN, and inference never needs it loaded — see
    ``ai/minimax_h3/h3_architecture_dossier.md`` §A.4). A tiny `adaln_t_table`
    (1025 rows x a small rank, 8 in the released checkpoint) is looked up and
    linearly interpolated over `t` directly into the modulation projection's
    input width, and every `adaln_proj.linear` runs on that raw interpolated
    value with **no SiLU** — the curve was trained to already be the
    activation's output, so re-applying it would double-activate. This is a
    real behavioural difference from the full checkpoint, not an optimisation;
    diffusers has no path for it at all (it cannot load a pruned checkpoint —
    the ~13B `adaln_proj.linear` tensors it expects are simply absent).

Forward decomposition matches this engine's other packed/staged families
(`_process_input` / `_prepare_timestep` / `_prepare_context` /
`_prepare_positional_embeddings` / `_process_transformer_blocks` /
`_process_output`). `_apply` invalidates the RoPE cache (position_ids can
change between generations sharing one loaded model); `post_load` recomputes
`rope.inv_freq` in fp32 (the checkpoint carries a copy, but a meta-constructed
buffer plus an assign-load is not trusted to leave a correct one — this
engine's standing rule for every rotary buffer).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from ...attention import attention as _dispatch_attention

# The OOM-rescue attention core (module-level so tests can stub it): plain
# SDPA on (B, H, L, D) -- see the fallback comment in MiniMaxH3Attention.
_fallback_sdpa = F.scaled_dot_product_attention
from ...base import NativeArchModule
from ...sla_attn import SlaAttnContext
from ...sol_attn import SolAttnContext
from ...sparse_attn import sparse_attention
from .config import MINIMAX_H3_MODALITY_NUM, MiniMaxH3Config


def _apply_rotary_emb(x: Tensor, cos: Tensor | None, sin: Tensor | None) -> Tensor:
    """Rotate the leading ``rotary_dim`` channels of every head; pass the rest through.

    ``x``: ``(B, S, H, D)``. ``cos``/``sin``: ``(S, rotary_dim)`` or ``None`` (no-op,
    used by the token refiner, which carries no rotary embedding at all).
    """
    if cos is None:
        return x
    rotary_dim = cos.shape[-1]
    x_rot, x_pass = x[..., :rotary_dim], x[..., rotary_dim:]
    cos = cos.to(x.dtype)[None, :, None, :]
    sin = sin.to(x.dtype)[None, :, None, :]
    x1, x2 = x_rot.chunk(2, dim=-1)
    x_rotated = torch.cat((-x2, x1), dim=-1)
    x_rot = x_rot * cos + x_rotated * sin
    return torch.cat((x_rot, x_pass), dim=-1)


def _h3_timestep_embedding(t: Tensor, dim: int = 256, max_period: float = 10000.0) -> Tensor:
    """Sinusoidal timestep embedding (flip_sin_to_cos, no frequency downscale).

    ``t`` is the raw unscaled-[0,1] timestep vector; the sinusoid itself always
    runs at ``max_period=10000`` regardless of ``rope_theta``. Returns
    ``(len(t), dim)`` float32, cos-half then sin-half.
    """
    half = dim // 2
    exponent = -math.log(max_period) * torch.arange(half, dtype=torch.float32, device=t.device) / half
    args = t.to(torch.float32)[:, None] * torch.exp(exponent)[None, :]
    return torch.cat((torch.cos(args), torch.sin(args)), dim=-1)


def _cast_to(x: Tensor, linear: nn.Module) -> Tensor:
    """Align an activation with a mixed-precision boundary Linear's own dtype."""
    return x.to(linear.weight.dtype)


class MiniMaxH3TimeEmbedder(nn.Module):
    """Full-mode only: sinusoidal timestep -> ``time_embed_dim`` MLP (fp32 module)."""

    def __init__(self, freq_dim: int, hidden_dim: int, out_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.freq_dim = freq_dim
        self.proj_in = operations.Linear(freq_dim, hidden_dim, bias=True, dtype=dtype, device=device)
        self.proj_out = operations.Linear(hidden_dim, out_dim, bias=True, dtype=dtype, device=device)

    def forward(self, timestep: Tensor) -> Tensor:
        emb = _h3_timestep_embedding(timestep, self.freq_dim)
        return self.proj_out(F.silu(self.proj_in(_cast_to(emb, self.proj_in))))


class MiniMaxH3AdalnProj(nn.Module):
    r"""Projects the shared timestep embedding into a block's (or the final
    layer's) per-(timestep, modality) modulation parameters.

    ``(num_timesteps, t_dim) -> expand tensors of (num_timesteps * modalities,
    hidden_size)``, row layout ``[t0_mod0, t0_mod1, t0_mod2, t1_mod0, ...]``
    (what ``timestep_indices * MINIMAX_H3_MODALITY_NUM + token_tags`` addresses
    for a block, or plain ``timestep_indices`` for the final layer's
    ``modalities=1``).

    ``apply_silu`` is the full-vs-pruned behavioural fork: the full checkpoint's
    ``time_embedder`` output gets SiLU'd here (at ITS OWN fp32 precision, then
    cast to this projection's — usually bf16 — dtype, so every block reads the
    activation at the same precision the reference does; skipping that ordering
    would bias every block's modulation identically at every step, accumulating
    coherently over the trajectory). The pruned checkpoint's ``adaln_t_table``
    lookup is already the post-activation curve, so applying SiLU a second time
    here would be wrong, not merely redundant.
    """

    def __init__(self, t_dim: int, hidden_size: int, expand: int, modalities: int, apply_silu: bool,
                 operations, dtype=None, device=None) -> None:
        super().__init__()
        self.hidden_size = hidden_size
        self.expand = expand
        self.modalities = modalities
        self.apply_silu = apply_silu
        self.linear = operations.Linear(t_dim, expand * hidden_size * modalities, bias=True, dtype=dtype, device=device)

    def forward(self, temb: Tensor) -> tuple[Tensor, ...]:
        x = F.silu(temb) if self.apply_silu else temb
        x = self.linear(_cast_to(x, self.linear))
        x = x.view(x.shape[0] * self.modalities, self.expand * self.hidden_size)
        return x.chunk(self.expand, dim=-1)


class MiniMaxH3Attention(nn.Module):
    """Full self-attention with a fused qkv projection and per-head RMSNorm(q,k)
    applied BEFORE RoPE. No cross-attention exists anywhere in this model."""

    def __init__(self, hidden_size: int, heads: int, head_dim: int, qk_norm_eps: float,
                 operations, dtype=None, device=None) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = head_dim
        inner_dim = heads * head_dim
        # Fused q|k|v on the OUTPUT axis: qkv_proj.weight is [3*inner_dim, hidden_size];
        # chunk(3, dim=-1) on the projection's output recovers (q, k, v) in that order.
        self.qkv_proj = operations.Linear(hidden_size, 3 * inner_dim, bias=False, dtype=dtype, device=device)
        self.q_norm = operations.RMSNorm(head_dim, eps=qk_norm_eps, dtype=dtype, device=device)
        self.k_norm = operations.RMSNorm(head_dim, eps=qk_norm_eps, dtype=dtype, device=device)
        self.out_proj = operations.Linear(inner_dim, hidden_size, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor, rotary_emb: tuple[Tensor, Tensor] | None,
                sparse_attn: SolAttnContext | SlaAttnContext | None = None,
                seq_chunk_rows: int = 0) -> Tensor:
        b, s, _ = x.shape
        if seq_chunk_rows and seq_chunk_rows < s:
            q, k, v = self._chunked_qkv(x, rotary_emb, seq_chunk_rows)
        else:
            q, k, v = self.qkv_proj(x).chunk(3, dim=-1)
            q = q.view(b, s, self.heads, self.head_dim)
            k = k.view(b, s, self.heads, self.head_dim)
            v = v.view(b, s, self.heads, self.head_dim)
            # Per-head RMSNorm BEFORE RoPE — order matters (dossier §A.5).
            q = self.q_norm(q)
            k = self.k_norm(k)
            cos, sin = rotary_emb if rotary_emb is not None else (None, None)
            q = _apply_rotary_emb(q, cos, sin)
            k = _apply_rotary_emb(k, cos, sin)
        # sparse_attention dispatches on the context's own type (Sol-Attn or
        # SLA) and consumes this pre-transpose BTHD layout directly. It
        # returns None whenever it did not run (off, dense-forced step,
        # unsupported machine, backend failure), which is the whole of the
        # opt-out: with sparse_attn=None nothing below this line is reached
        # and the dense path is byte-for-byte what it was before either
        # option existed.
        sparse = sparse_attention(q, k, v, sparse_attn)
        if sparse is not None:
            out = sparse.reshape(b, s, -1)
        else:
            # MiniMax-H3 packs one request into a single attention document: no
            # mask, ever (see the module docstring), so every attention backend
            # is eligible. The attention core itself is never chunked up front —
            # its kernels are memory-bounded — but the backends' own quant/copy
            # transients (sage: q_int8/k_int8/v_fp8 + padding) can still tip a
            # nearly-full card over at very long sequences, so an OOM here
            # retries QUERY-chunked: softmax over the full key set per query
            # row, so the math is exact; only peak memory (and, for sage, a
            # per-chunk k/v re-quant) changes.
            qT, kT, vT = q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)
            try:
                out = _dispatch_attention(qT, kT, vT, heads=self.heads, mask=None)
            except torch.OutOfMemoryError:
                # Retry with query-chunked SDPA, NOT the normal dispatch: sage
                # re-quantizes the FULL key/value set per call (k_int8 +
                # v_transposed_permutted + v_fp8, several GB at ~78k rows), so
                # a sage retry keeps the exact transients that just OOM'd.
                # SDPA's flash/mem-efficient kernels allocate no full-size
                # copies, need no V-prescale guard (no fp16 quant internals),
                # and full-key softmax per query chunk is exact.
                chunk = seq_chunk_rows if seq_chunk_rows else 16384
                logging.getLogger(__name__).warning(
                    "[MINIMAX_H3] attention OOM at %d rows; retrying query-chunked sdpa (%d rows/call)",
                    s, chunk,
                )
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                out = torch.cat(
                    [_fallback_sdpa(qc, kT, vT) for qc in qT.split(chunk, dim=2)],
                    dim=2,
                )
            out = out.transpose(1, 2).reshape(b, s, -1)
        if seq_chunk_rows and seq_chunk_rows < s:
            return torch.cat([self.out_proj(c) for c in out.split(seq_chunk_rows, dim=1)], dim=1)
        return self.out_proj(out)

    def _chunked_qkv(self, x: Tensor, rotary_emb: tuple[Tensor, Tensor] | None,
                      chunk_rows: int) -> tuple[Tensor, Tensor, Tensor]:
        """Row-chunked qkv projection + per-head RMSNorm(q,k) + RoPE.

        The fused ``qkv_proj`` output (``3*inner_dim`` wide) and the norm/RoPE
        transients derived from it are the memory cost being bounded here — q,
        k and v themselves must exist FULL-LENGTH for the attention core below,
        so this writes each chunk's result into preallocated full-size buffers
        rather than deferring the concatenation. Every op inside the loop
        (linear projection, per-row RMSNorm, per-row RoPE) is row-independent,
        so this is exact under chunking regardless of chunk size or a ragged
        final chunk — see ``test_seq_chunk_rows_matches_unchunked_output``.
        """
        b, s, _ = x.shape
        cos, sin = rotary_emb if rotary_emb is not None else (None, None)
        q_full = x.new_empty(b, s, self.heads, self.head_dim)
        k_full = x.new_empty(b, s, self.heads, self.head_dim)
        v_full = x.new_empty(b, s, self.heads, self.head_dim)
        start = 0
        for x_chunk in x.split(chunk_rows, dim=1):
            n = x_chunk.shape[1]
            q_c, k_c, v_c = self.qkv_proj(x_chunk).chunk(3, dim=-1)
            q_c = self.q_norm(q_c.view(b, n, self.heads, self.head_dim))
            k_c = self.k_norm(k_c.view(b, n, self.heads, self.head_dim))
            v_c = v_c.view(b, n, self.heads, self.head_dim)
            if cos is not None:
                q_c = _apply_rotary_emb(q_c, cos[start:start + n], sin[start:start + n])
                k_c = _apply_rotary_emb(k_c, cos[start:start + n], sin[start:start + n])
            q_full[:, start:start + n] = q_c
            k_full[:, start:start + n] = k_c
            v_full[:, start:start + n] = v_c
            start += n
        return q_full, k_full, v_full


class MiniMaxH3MLP(nn.Module):
    """SwiGLU feed-forward: ``fc1`` fuses the gate and value projections; the
    GATE is the FIRST half of ``fc1``'s output, the value the second.

    This is the Comfy-Org single-file repack's own convention (verified
    against ``comfy/ops.py``'s ``_swiglu_eager`` -- the real, working consumer
    of this exact checkpoint format: ``gate, up = x.chunk(2, dim=-1); return
    silu(gate) * up``) -- NOT diffusers' own (unfused, differently-ordered)
    ``SwiGLU`` module, which puts value first and gate second. A previous
    version of this port copied diffusers' ordering, which is backwards for
    this checkpoint and silently corrupted every block's FFN output (per-row,
    hence per-patch -- this was the "hundreds of squares" structured-noise
    bug, not a crash or a shape mismatch)."""

    def __init__(self, hidden_size: int, ffn_dim: int, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.fc1 = operations.Linear(hidden_size, 2 * ffn_dim, bias=False, dtype=dtype, device=device)
        self.fc2 = operations.Linear(ffn_dim, hidden_size, bias=False, dtype=dtype, device=device)

    def forward(self, x: Tensor, seq_chunk_rows: int = 0) -> Tensor:
        if seq_chunk_rows and seq_chunk_rows < x.shape[-2]:
            # Row-chunked over the sequence axis so the ``2*ffn_dim`` fc1
            # output only ever exists chunk-sized -- fc1/fc2 are per-row
            # linears, exact under chunking regardless of a ragged tail.
            chunks = []
            for x_chunk in x.split(seq_chunk_rows, dim=-2):
                gate, value = self.fc1(x_chunk).chunk(2, dim=-1)
                chunks.append(self.fc2(F.silu(gate) * value))
            return torch.cat(chunks, dim=-2)
        gate, value = self.fc1(x).chunk(2, dim=-1)
        return self.fc2(F.silu(gate) * value)


class MiniMaxH3RefinerBlock(nn.Module):
    """Plain pre-norm block for the text-stream refiner: no AdaLN, no RoPE."""

    def __init__(self, hidden_size: int, heads: int, head_dim: int, ffn_dim: int,
                 norm_eps: float, qk_norm_eps: float, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.norm1 = operations.RMSNorm(hidden_size, eps=norm_eps, dtype=dtype, device=device)
        self.attn = MiniMaxH3Attention(hidden_size, heads, head_dim, qk_norm_eps, operations, dtype=dtype, device=device)
        self.norm2 = operations.RMSNorm(hidden_size, eps=norm_eps, dtype=dtype, device=device)
        self.mlp = MiniMaxH3MLP(hidden_size, ffn_dim, operations, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        x = x + self.attn(self.norm1(x), rotary_emb=None)
        x = x + self.mlp(self.norm2(x))
        return x


class MiniMaxH3TokenRefiner(nn.Module):
    def __init__(self, hidden_size: int, heads: int, head_dim: int, ffn_dim: int, num_layers: int,
                 norm_eps: float, qk_norm_eps: float, final_norm_eps: float,
                 operations, dtype=None, device=None) -> None:
        super().__init__()
        self.blocks = nn.ModuleList([
            MiniMaxH3RefinerBlock(hidden_size, heads, head_dim, ffn_dim, norm_eps, qk_norm_eps,
                                   operations, dtype=dtype, device=device)
            for _ in range(num_layers)
        ])
        self.final_norm = operations.RMSNorm(hidden_size, eps=final_norm_eps, dtype=dtype, device=device)

    def forward(self, x: Tensor) -> Tensor:
        for block in self.blocks:
            x = block(x)
        return self.final_norm(x)


class MiniMaxH3Block(nn.Module):
    """Pre-norm self-attention + SwiGLU, each modulated by AdaLN parameters
    selected per row of the packed sequence from the (timestep, modality) table."""

    def __init__(self, hidden_size: int, heads: int, head_dim: int, ffn_dim: int, t_dim: int,
                 apply_silu: bool, norm_eps: float, qk_norm_eps: float,
                 operations, dtype=None, device=None) -> None:
        super().__init__()
        self.norm1 = operations.RMSNorm(hidden_size, eps=norm_eps, dtype=dtype, device=device)
        self.attn = MiniMaxH3Attention(hidden_size, heads, head_dim, qk_norm_eps, operations, dtype=dtype, device=device)
        self.norm2 = operations.RMSNorm(hidden_size, eps=norm_eps, dtype=dtype, device=device)
        self.mlp = MiniMaxH3MLP(hidden_size, ffn_dim, operations, dtype=dtype, device=device)
        self.adaln_proj = MiniMaxH3AdalnProj(t_dim, hidden_size, 6, MINIMAX_H3_MODALITY_NUM, apply_silu,
                                              operations, dtype=dtype, device=device)

    def forward(self, x: Tensor, temb: Tensor, adaln_indices: Tensor, rotary_emb: tuple[Tensor, Tensor],
                sparse_attn: SolAttnContext | SlaAttnContext | None = None,
                seq_chunk_rows: int = 0) -> Tensor:
        # The AdaLN table inherits temb's fp32 (the time embedder is an fp32
        # island); applying it as-is would promote the whole packed stream to
        # fp32 for every block downstream — fp32 attention loses the flash
        # path and fp32 linears double the dequant cost (same residual-stream
        # promotion Krea-2 hit). The table is (num_timesteps*3, hidden), so
        # casting it is free; the projection itself stays fp32.
        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
            m.to(x.dtype) for m in self.adaln_proj(temb)
        )

        # The modulation multiply/add below is per-row and elementwise (not
        # the memory cost seq_chunk_rows targets, see model.py's forward-stage
        # docstring), so it stays a plain full-tensor op; only attn/mlp's own
        # internal transients get chunked.
        residual = x
        h = self.norm1(x)
        h = h * (1.0 + scale_msa.index_select(0, adaln_indices)) + shift_msa.index_select(0, adaln_indices)
        h = self.attn(h, rotary_emb, sparse_attn, seq_chunk_rows)
        x = residual + gate_msa.index_select(0, adaln_indices) * h

        residual = x
        h = self.norm2(x)
        h = h * (1.0 + scale_mlp.index_select(0, adaln_indices)) + shift_mlp.index_select(0, adaln_indices)
        h = self.mlp(h, seq_chunk_rows)
        x = residual + gate_mlp.index_select(0, adaln_indices) * h
        return x


class MiniMaxH3FinalLayer(nn.Module):
    """Shared output norm + the two per-modality output heads.

    Both heads run over EVERY row of the packed sequence (including
    conditioning/text rows that get discarded downstream) — cheaper
    slice-then-project designs are equivalent for the rows that survive, but
    this module follows the reference contract as written so the two are
    trivially auditable against each other.
    """

    def __init__(self, hidden_size: int, t_dim: int, video_dim: int, audio_dim: int, eps: float,
                 apply_silu: bool, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.norm = operations.RMSNorm(hidden_size, eps=eps, dtype=dtype, device=device)
        self.adaln_proj = MiniMaxH3AdalnProj(t_dim, hidden_size, 2, 1, apply_silu, operations, dtype=dtype, device=device)
        # The output heads are one of this checkpoint's fp32 islands.
        self.video_out = operations.Linear(hidden_size, video_dim, bias=True, dtype=torch.float32, device=device)
        self.audio_out = operations.Linear(hidden_size, audio_dim, bias=True, dtype=torch.float32, device=device)

    def forward(self, x: Tensor, temb: Tensor, timestep_indices: Tensor) -> tuple[Tensor, Tensor]:
        # split shift-then-scale, indexed by TIMESTEP only (not modality).
        shift, scale = self.adaln_proj(temb)
        shift = shift.index_select(0, timestep_indices)
        scale = scale.index_select(0, timestep_indices)
        h = self.norm(x) * (1.0 + scale) + shift
        h = _cast_to(h, self.video_out)
        return self.video_out(h), self.audio_out(h)


class MiniMaxH3Model(NativeArchModule):
    """MiniMax-H3 packed-sequence DiT (construction + load path + forward)."""

    def __init__(self, config: MiniMaxH3Config, operations, dtype=None, device=None) -> None:
        super().__init__()
        self.config = config
        self.operations = operations
        hidden_size = config.hidden_size
        video_patch_dim = config.video_patch_dim

        # 1. Per-modality input projections. The two patch projections are one
        # of this checkpoint's fp32 islands; condition_proj runs at the block
        # stack's own (usually bf16) dtype.
        self.video_patch_proj = operations.Linear(video_patch_dim, hidden_size, bias=True,
                                                    dtype=torch.float32, device=device)
        self.audio_patch_proj = operations.Linear(config.audio_in_channels, hidden_size, bias=True,
                                                    dtype=torch.float32, device=device)
        self.condition_proj = operations.Linear(config.text_dim, hidden_size, bias=True, dtype=dtype, device=device)

        # 2. Timestep embedding, shared by every AdaLN projection (blocks + final
        # layer). Full checkpoints carry a time_embedder MLP; pruned checkpoints
        # carry a small interpolation table instead (see the module docstring).
        if config.pruned:
            self.register_buffer(
                "adaln_t_table",
                torch.empty(config.adaln_curve_grid, config.time_embed_dim, dtype=torch.float32, device=device),
            )
        else:
            self.time_embedder = MiniMaxH3TimeEmbedder(
                config.freq_dim, config.time_embed_hidden_dim, config.time_embed_dim,
                operations, dtype=torch.float32, device=device,
            )

        # 3. Rotary embedding buffer. Present in the checkpoint (unlike the
        # diffusers reference, which never persists it) but recomputed in
        # post_load regardless — this engine's standing rotary-buffer rule.
        self.rope = nn.Module()
        self.rope.register_buffer("inv_freq", torch.empty(config.rope_freq_dim, dtype=torch.float32, device=device))

        # 4. Text-stream refiner (no AdaLN, no RoPE).
        self.token_refiner = MiniMaxH3TokenRefiner(
            hidden_size, config.num_attention_heads, config.attention_head_dim, config.ffn_dim,
            config.num_refiner_layers, config.norm_eps, config.qk_norm_eps, config.final_norm_eps,
            operations, dtype=dtype, device=device,
        )

        # 5. The block stack.
        apply_silu = not config.pruned
        self.blocks = nn.ModuleList([
            MiniMaxH3Block(hidden_size, config.num_attention_heads, config.attention_head_dim, config.ffn_dim,
                            config.time_embed_dim, apply_silu, config.norm_eps, config.qk_norm_eps,
                            operations, dtype=dtype, device=device)
            for _ in range(config.num_layers)
        ])

        # 6. Shared output norm + the two per-modality output heads.
        self.final_layer = MiniMaxH3FinalLayer(
            hidden_size, config.time_embed_dim, video_patch_dim, config.audio_in_channels,
            config.final_norm_eps, apply_silu, operations, dtype=dtype, device=device,
        )

        # Load-time contract: every non-VAE arch module exposes patch_size.
        self.patch_size = config.patch_size

        # Per-generation RoPE cos/sin cache: position_ids is fixed for every
        # denoise step of one generation (the caller builds it once), so the
        # (t,h,w) grid only needs projecting through inv_freq once instead of
        # once per step. Plain instance attributes (never enter the
        # state-dict); _apply() drops them on any device/dtype move.
        self._pe_cache_key: tuple | None = None
        self._pe_cache: tuple[Tensor, Tensor] | None = None

    def _apply(self, fn, recurse: bool = True):
        self._pe_cache_key = None
        self._pe_cache = None
        return super()._apply(fn, recurse=recurse)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxH3Model":
        return cls(MiniMaxH3Config.from_detect_config(config), operations=operations)

    def post_load(self) -> None:
        """Recompute ``rope.inv_freq`` in fp32 (the checkpoint's copy is not
        trusted — this engine's standing rotary-buffer rule). Nothing else is
        derived: ``adaln_t_table`` (pruned) is a loaded checkpoint tensor, not
        computed state, and the sinusoidal timestep embedding (full) is built
        fresh every forward from the raw timestep vector."""
        device = self.video_patch_proj.weight.device
        rope_freq_dim = self.config.rope_freq_dim
        theta = self.config.rope_theta
        inv_freq = 1.0 / (theta ** (torch.arange(0, 2 * rope_freq_dim, 2, dtype=torch.float32, device=device) / (2 * rope_freq_dim)))
        self.rope.inv_freq = inv_freq

    # -- forward stages -------------------------------------------------------

    def _process_input(self, hidden_states: Tensor, audio_hidden_states: Tensor) -> tuple[Tensor, Tensor]:
        video_embeds = self.video_patch_proj(_cast_to(hidden_states, self.video_patch_proj))
        audio_embeds = self.audio_patch_proj(_cast_to(audio_hidden_states, self.audio_patch_proj))
        return video_embeds, audio_embeds

    def _prepare_context(self, encoder_hidden_states: Tensor) -> Tensor:
        text_embeds = self.condition_proj(_cast_to(encoder_hidden_states, self.condition_proj))
        return self.token_refiner(text_embeds)

    def _lookup_adaln_curve(self, timestep: Tensor) -> Tensor:
        """Pruned-mode AdaLN: linear interpolation of ``adaln_t_table`` over
        ``t``. The table is a uniform grid over ``t in [0, 1]``; ``t`` is
        mapped to a fractional row index, floor-clamped to the second-to-last
        row so ``t == 1.0`` still interpolates the final interval instead of
        reading past the table, and the two neighbouring rows are blended by
        the fractional part."""
        table = self.adaln_t_table.to(device=timestep.device, dtype=torch.float32)
        grid = table.shape[0]
        pos = timestep.to(torch.float32).clamp(0.0, 1.0) * (grid - 1)
        i0 = pos.floor().long().clamp(max=grid - 2)
        frac = (pos - i0).unsqueeze(-1)
        return torch.lerp(table[i0], table[i0 + 1], frac)

    def _prepare_timestep(self, timestep: Tensor) -> Tensor:
        if self.config.pruned:
            return self._lookup_adaln_curve(timestep)
        return self.time_embedder(timestep)

    def _prepare_positional_embeddings(self, position_ids: Tensor) -> tuple[Tensor, Tensor]:
        device = position_ids.device
        cache_key = (id(position_ids), int(position_ids._version), tuple(position_ids.shape), str(device))
        if self._pe_cache_key == cache_key:
            return self._pe_cache
        inv_freq = self.rope.inv_freq.to(device=device, dtype=torch.float32)
        pos = position_ids.to(torch.float32)
        freqs = pos.unsqueeze(-1) * inv_freq.view(1, 1, -1)          # (S, 3, rope_freq_dim)
        freqs_t, freqs_h, freqs_w = freqs.unbind(dim=1)
        freqs = torch.cat((freqs_t, freqs_h, freqs_w), dim=-1)       # (S, 3*rope_freq_dim)
        freqs = torch.cat((freqs, freqs), dim=-1)                    # (S, 2*3*rope_freq_dim)
        result = (freqs.cos(), freqs.sin())
        self._pe_cache_key = cache_key
        self._pe_cache = result
        return result

    def _process_transformer_blocks(self, hidden_states: Tensor, temb: Tensor, adaln_indices: Tensor,
                                     rotary_emb: tuple[Tensor, Tensor],
                                     step_cache=None,
                                     sparse_attn: SolAttnContext | SlaAttnContext | None = None,
                                     seq_chunk_rows: int = 0,
                                     ) -> tuple[Tensor, Tensor | None, bool]:
        """Run the block stack, optionally gated by FBCache (see
        ``sampling/step_cache.py``).

        The probe is block-0's output over the WHOLE packed sequence, which
        already covers video, audio and text rows in one tensor — H3 runs a
        single stream, so there is nothing to concatenate the way LTX's
        separate video/audio streams need. A skip returns straight after block
        0, so blocks 1..N *and* the final layer are bypassed; ``forward``
        replays the cached ``(video, audio)`` pair instead.

        ``sparse_attn`` reaches only this stack. The token refiner's blocks run
        a short text-only sequence with no rotary embedding and no packed
        prefix to keep exact — nothing either sparse-attention method's
        routing has anything to route over.
        """
        probe = None
        for i, block in enumerate(self.blocks):
            hidden_states = block(hidden_states, temb, adaln_indices, rotary_emb, sparse_attn, seq_chunk_rows)
            if i == 0 and step_cache is not None:
                probe = hidden_states
                if step_cache.should_skip(probe):
                    return hidden_states, probe, True
        return hidden_states, probe, False

    def _process_output(self, hidden_states: Tensor, temb: Tensor, timestep_indices: Tensor,
                         video_indices: Tensor, audio_indices: Tensor) -> tuple[Tensor, Tensor]:
        video_full, audio_full = self.final_layer(hidden_states, temb, timestep_indices)
        return video_full.index_select(1, video_indices), audio_full.index_select(1, audio_indices)

    # -- forward --------------------------------------------------------------

    def forward(
        self,
        hidden_states: Tensor,
        audio_hidden_states: Tensor,
        encoder_hidden_states: Tensor,
        timestep: Tensor,
        timestep_indices: Tensor,
        token_tags: Tensor,
        position_ids: Tensor,
        video_indices: Tensor,
        audio_indices: Tensor,
        text_indices: Tensor,
        **kwargs: Any,
    ) -> tuple[Tensor, Tensor]:
        """One packed-sequence forward. See the module docstring: the caller
        builds the layout (row order, positions, per-row timestep/modality
        tags) — this model only projects, scatters, runs the block stack, and
        gathers the two output modalities back out.

        ``hidden_states``: ``(B, num_video_tokens, video_patch_dim)`` patchified
        video rows (conditioning AND target, in packed order). ``audio_hidden_
        states``: ``(B, num_audio_tokens, audio_in_channels)``. ``encoder_
        hidden_states``: ``(B, num_text_tokens, text_dim)``. ``timestep``:
        ``(num_timesteps,)`` distinct noise levels in ``[0, 1]``, unscaled.
        ``timestep_indices`` / ``token_tags``: ``(seq_len,)`` per-row lookup
        into ``timestep`` / modality (0 video, 1 text, 2 audio). ``position_
        ids``: ``(seq_len, 3)`` rotary ``(t, h, w)`` coordinates. ``video_
        indices`` / ``audio_indices`` / ``text_indices``: positions of each
        modality's rows in the packed sequence.

        ``step_cache`` (keyword, optional): a
        :class:`~src.platform.runtime.native.sampling.step_cache.FirstBlockCache`
        that may replay the previous step's ``(video, audio)`` pair instead of
        running blocks 1..N. H3 is guidance-distilled (one branch), so the
        caller passes a single cache rather than a ``StepCacheSet``.

        ``sparse_attn_ctx`` (keyword, optional): a
        :class:`~src.platform.runtime.native.sol_attn.SolAttnContext` or
        :class:`~src.platform.runtime.native.sla_attn.SlaAttnContext` opting
        the block stack's self-attention into one of the two sparse-attention
        methods — dispatched by
        :func:`~src.platform.runtime.native.sparse_attn.sparse_attention`,
        which reads the context's own type. Absent (the default) the forward
        is bit-identical to one without the feature. The two are independent
        of FBCache: a step the cache skips never reaches attention at all, and
        a dense-forced sparse-attention step is an ordinary cached-or-computed
        step.

        ``seq_chunk_rows`` (keyword, optional): low-VRAM sequence chunking.
        ``0`` (default) is off and byte-identical to a build without the
        feature. Above ``0``, every block's qkv projection + RMSNorm(q,k) +
        RoPE, and its SwiGLU MLP, run row-chunked over the packed sequence
        instead of materializing their full-length intermediate transients at
        once (the ``2*ffn_dim`` SwiGLU output and the fused ``3*inner_dim``
        qkv projection are the two that OOM first on a long refine sequence).
        The attention core itself is never chunked. A quantized DiT
        (fp8/int8) dequantizes its weight on every ``Linear.forward`` call, so
        this trades a smaller peak transient for one extra dequant per chunk
        per chunked Linear — pick the chunk size accordingly.
        """
        if position_ids.ndim != 2 or position_ids.shape[-1] != 3:
            raise ValueError(f"position_ids must be (seq_len, 3), got {list(position_ids.shape)}")
        seq_len = position_ids.shape[0]
        if token_tags.shape != (seq_len,) or timestep_indices.shape != (seq_len,):
            raise ValueError(
                "token_tags and timestep_indices must both be (seq_len,) tensors matching "
                f"position_ids, got {list(token_tags.shape)} and {list(timestep_indices.shape)} "
                f"for seq_len={seq_len}"
            )

        rotary_emb = self._prepare_positional_embeddings(position_ids)

        video_embeds, audio_embeds = self._process_input(hidden_states, audio_hidden_states)
        text_embeds = self._prepare_context(encoder_hidden_states)

        # The text stream sets the packed buffer's dtype (matches diffusers'
        # own contract for this scatter).
        hidden_dtype = text_embeds.dtype
        packed = text_embeds.new_zeros((text_embeds.shape[0], seq_len, self.config.hidden_size))
        packed = packed.index_copy(1, text_indices, text_embeds)
        packed = packed.index_copy(1, video_indices, video_embeds.to(hidden_dtype))
        packed = packed.index_copy(1, audio_indices, audio_embeds.to(hidden_dtype))

        temb = self._prepare_timestep(timestep)
        adaln_indices = timestep_indices * MINIMAX_H3_MODALITY_NUM + token_tags

        step_cache = kwargs.pop("step_cache", None)
        sparse_attn_ctx = kwargs.pop("sparse_attn_ctx", None)
        seq_chunk_rows = kwargs.pop("seq_chunk_rows", 0)
        packed, probe, skipped = self._process_transformer_blocks(
            packed, temb, adaln_indices, rotary_emb, step_cache=step_cache, sparse_attn=sparse_attn_ctx,
            seq_chunk_rows=seq_chunk_rows,
        )
        if skipped:
            return step_cache.record_skip()

        result = self._process_output(packed, temb, timestep_indices, video_indices, audio_indices)
        if step_cache is not None and probe is not None:
            step_cache.record_compute(probe, result)
        return result
