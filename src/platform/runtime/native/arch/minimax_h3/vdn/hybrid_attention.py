# Derived from: OpenVDN/vdn-minimax-h3 (Apache-2.0) `src/models/hybrid_attention.py`
# — the two-branch fusion: one shared fused projection, the windowed softmax gated and
# sent through the ORIGINAL output projection, the linear readout added to the video
# rows only.

"""The VDN hybrid attention module: the orchestrator over the two landed branches.

    window_out = windowed_softmax(q, k, v)            # QK-normed + RoPE'd, as the base
    out        = out_proj(softmax_gate(x) * window_out)
    out[video] += linear_branch(x, q_raw, k_raw, v_raw)

Both branches read the SAME fused ``qkv_proj`` output, so a LoRA merged into that
projection reaches both and the hybrid pays no extra projection cost. The softmax side
consumes the normed and rotated q/k; the linear side consumes the raw pre-norm,
pre-RoPE q/k/v.

Without a layout this is the base attention, delegated rather than re-implemented, so a
model with the branch attached and no layout is byte-identical to one without it.
"""

from __future__ import annotations

from torch import Tensor, nn

from ..model import MiniMaxH3Attention, _apply_rotary_emb
from .layout import VdnLayout
from .linear_branch import MiniMaxH3LinearBranch
from .window import covers_all_frames, windowed_softmax

__all__ = ["MiniMaxH3VdnAttention"]


class MiniMaxH3VdnAttention(MiniMaxH3Attention):
    """One DiT block's attention, split into a windowed softmax and a linear branch.

    The base attention's submodules are adopted under their OWN names rather than
    moved under a child module: the LoRA key map resolves
    ``transformer_blocks.N.attn.to_q`` (and an adapter's ``attn.orig.to_q`` spelling)
    onto the native name ``blocks.N.attn.qkv_proj``, so renaming the projections here
    would silently drop every adapter. There is no ``orig`` attribute at all — the base
    module object is not kept, because two live objects would mean two copies of the
    sticky ``_sdpa_min_rows`` OOM marker and only one of them would ever be updated.
    Delegation goes through ``super().forward``, which sees exactly the attributes it
    needs on ``self``.
    """

    vdn_branch_attached = True

    def __init__(self, base: MiniMaxH3Attention, hidden_size: int, operations, *,
                 anchor_frames: str = "both", backend: str | None = None,
                 dtype=None, device=None) -> None:
        # NOT super().__init__(): the base constructor would build a second set of
        # projections, and this module adopts the ones it was handed.
        nn.Module.__init__(self)
        self.heads = base.heads
        self.head_dim = base.head_dim
        self.qkv_proj = base.qkv_proj
        self.q_norm = base.q_norm
        self.k_norm = base.k_norm
        self.out_proj = base.out_proj
        self._sdpa_min_rows = base._sdpa_min_rows
        self.anchor_frames = anchor_frames
        self.backend = backend
        self.linear = MiniMaxH3LinearBranch(
            hidden_size, base.heads, base.head_dim, operations, dtype=dtype, device=device
        )

    def forward(self, x: Tensor, rotary_emb: tuple[Tensor, Tensor] | None,
                sparse_attn=None, seq_chunk_rows: int = 0,
                vdn_layout: VdnLayout | None = None) -> Tensor:
        if vdn_layout is None:
            return super().forward(x, rotary_emb, sparse_attn, seq_chunk_rows)
        if sparse_attn is not None or seq_chunk_rows:
            raise ValueError(
                "VDN re-decides which keys every query sees and its linear branch needs "
                "all video rows at once, so it is mutually exclusive with sparse "
                f"attention (sparse_attn={type(sparse_attn).__name__ if sparse_attn is not None else None}) "
                f"and sequence chunking (seq_chunk_rows={seq_chunk_rows}); drop one of them"
            )
        b, s, _ = x.shape
        if b != 1:
            raise ValueError(
                f"VDN runs one packed document per forward, so batch must be 1, got {b}"
            )
        if s != vdn_layout.seq_len:
            raise ValueError(
                f"{s} rows but the layout describes {vdn_layout.seq_len}"
            )
        if vdn_layout.skip_ends != (self.anchor_frames == "both"):
            # Set one without the other and the two branches either overlap on frames
            # 0 and F-1 or leave them covered by neither — silently, in both cases.
            raise ValueError(
                f"layout.skip_ends={vdn_layout.skip_ends} does not pair with "
                f"anchor_frames={self.anchor_frames!r}; build the layout with "
                "VdnLayout.with_anchor_mode(anchor_frames)"
            )
        if covers_all_frames(vdn_layout.num_frames, vdn_layout.window_bounds, self.anchor_frames):
            # A window wide enough to cover every frame IS the dense attention, and
            # nothing is left outside it for the branch to own — running the branch
            # here would double-count. Decided before the projection rather than on
            # windowed_softmax's None return, so the full-cover path projects once.
            return super().forward(x, rotary_emb, sparse_attn, seq_chunk_rows)

        q_raw, k_raw, v_raw = self.qkv_proj(x).chunk(3, dim=-1)
        q_raw = q_raw.view(b, s, self.heads, self.head_dim)
        k_raw = k_raw.view(b, s, self.heads, self.head_dim)
        v_raw = v_raw.view(b, s, self.heads, self.head_dim)
        cos, sin = rotary_emb if rotary_emb is not None else (None, None)
        query = _apply_rotary_emb(self.q_norm(q_raw), cos, sin).squeeze(0)
        key = _apply_rotary_emb(self.k_norm(k_raw), cos, sin).squeeze(0)
        q_raw, k_raw, v_raw = q_raw.squeeze(0), k_raw.squeeze(0), v_raw.squeeze(0)

        window_out = windowed_softmax(
            query, key, v_raw, vdn_layout,
            heads=self.heads, anchor_frames=self.anchor_frames, backend=self.backend,
        )
        # The rotated q/k are dead here and are ~3 GiB at H3 scale; they would
        # otherwise sit under the linear branch's own peak.
        del query, key

        rows = x[0]
        out = self.out_proj((self.linear.softmax_gate(rows) * window_out).reshape(b, s, -1))
        del window_out
        readout = self.linear(rows, q_raw, k_raw, v_raw, vdn_layout)
        out[0, vdn_layout.video_start:vdn_layout.video_end] += readout.to(out.dtype)
        return out
