# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/structured_latent_flow.py
# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/transformer/modulated.py
# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/attention/modules.py
# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sparse_structure_flow.py (TimestepEmbedder only)
"""SLat flow DiT — the sparse structured-latent transformer TRELLIS.2 runs for
both the shape and texture flows (``model.img2shape*.`` / ``model.shape2txt.``
checkpoint prefixes select which one a given weight file is).

Ports upstream's ``SLatFlowModel`` + ``ModulatedSparseTransformerCrossBlock``
onto ``sparse3d`` (this port's own ``SparseTensor``, not upstream's
torchsparse/spconv-backed one). Two upstream pieces are intentionally not
carried over because sparse3d has no equivalent and no caller here needs one:

* ``pe_mode="ape"`` (upstream's ``AbsolutePositionEmbedder`` path) — every
  production SLat flow config uses ``pe_mode="rope"``.
* the ``VarLenTensor``-packed cross-attention context — the texture/shape
  flow's ``cond`` is always a dense per-batch tensor ``[N, L, cond_channels]``
  (image/text embeddings), never a packed variable-length sparse stream, so
  cross-attention here only implements the dense-kv path. Doing so needs a
  small sparse-query/dense-kv attention helper (``_sparse_dense_cross_attention``
  below) that has no sparse3d counterpart to live in instead.

Upstream's mixed-precision plumbing (``manual_cast``/``convert_to``/``dtype``
str parsing) is also dropped: this port runs everything in one dtype, decided
by the caller the way every other native arch module here does.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...sparse3d import (
    SparseLinear,
    SparseRotaryPositionEmbedder,
    SparseTensor,
    sparse_cat,
    sparse_scaled_dot_product_attention,
)
from ...attention import attention as native_attention

__all__ = ["SLatFlowModel"]


class _TimestepEmbedder(nn.Module):
    """Sinusoidal timestep -> vector embedding (attribute name ``t_embedder``
    on ``SLatFlowModel`` matches upstream's checkpoint key space)."""

    def __init__(self, hidden_size: int, frequency_embedding_size: int = 256):
        super().__init__()
        self.mlp = nn.Sequential(
            nn.Linear(frequency_embedding_size, hidden_size, bias=True),
            nn.SiLU(),
            nn.Linear(hidden_size, hidden_size, bias=True),
        )
        self.frequency_embedding_size = frequency_embedding_size

    @staticmethod
    def _timestep_embedding(t: torch.Tensor, dim: int, max_period: int = 10000) -> torch.Tensor:
        half = dim // 2
        freqs = torch.exp(
            -math.log(max_period) * torch.arange(start=0, end=half, dtype=torch.float32, device=t.device) / half
        )
        args = t[:, None].float() * freqs[None]
        embedding = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
        if dim % 2:
            embedding = torch.cat([embedding, torch.zeros_like(embedding[:, :1])], dim=-1)
        return embedding

    def forward(self, t: torch.Tensor) -> torch.Tensor:
        return self.mlp(self._timestep_embedding(t, self.frequency_embedding_size))


class _SparseMultiHeadRMSNorm(nn.Module):
    def __init__(self, dim: int, heads: int):
        super().__init__()
        self.scale = dim**0.5
        self.gamma = nn.Parameter(torch.ones(heads, dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_dtype = x.dtype
        x = F.normalize(x.float(), dim=-1) * self.gamma * self.scale
        return x.to(x_dtype)


def _sparse_dense_cross_attention(q: SparseTensor, k: torch.Tensor, v: torch.Tensor) -> SparseTensor:
    """``q``: sparse ``[T, H, D]``. ``k``/``v``: dense per-batch ``[N, L, H, D]``
    (the cond stream is never ragged). Slices ``q`` per batch element the same
    way ``sparse3d.attention.sparse_scaled_dot_product_attention`` does, but
    pairs each slice with a dense ``k``/``v`` row instead of a sparse one."""
    chunks = []
    for i, q_slice in enumerate(q.layout):
        q_i = q.feats[q_slice].transpose(0, 1).unsqueeze(0)  # [1, H, Lq, D]
        k_i = k[i].transpose(0, 1).unsqueeze(0)  # [1, H, Lkv, D]
        v_i = v[i].transpose(0, 1).unsqueeze(0)
        o_i = native_attention(q_i, k_i, v_i)
        chunks.append(o_i.squeeze(0).transpose(0, 1))
    out = torch.cat(chunks, dim=0)
    return q.replace(out)


class _SparseMultiHeadAttention(nn.Module):
    """Self- or cross-attention over a ``SparseTensor``. Mirrors upstream's
    ``SparseMultiHeadAttention`` closely enough that its parameter names
    (``to_qkv`` / ``to_q``+``to_kv`` / ``to_out`` / ``q_rms_norm`` /
    ``k_rms_norm`` / ``rope``) match the checkpoint key space exactly."""

    def __init__(
        self,
        channels: int,
        num_heads: int,
        ctx_channels: Optional[int] = None,
        attn_type: str = "self",
        qkv_bias: bool = True,
        use_rope: bool = False,
        rope_freq: Tuple[float, float] = (1.0, 10000.0),
        qk_rms_norm: bool = False,
    ):
        super().__init__()
        assert channels % num_heads == 0
        assert attn_type in ("self", "cross")
        assert attn_type == "self" or not use_rope, "rope is only defined for self-attention"
        self.num_heads = num_heads
        self.head_dim = channels // num_heads
        self.attn_type = attn_type
        self.use_rope = use_rope
        self.qk_rms_norm = qk_rms_norm

        if attn_type == "self":
            self.to_qkv = nn.Linear(channels, channels * 3, bias=qkv_bias)
        else:
            ctx_channels = ctx_channels if ctx_channels is not None else channels
            self.to_q = nn.Linear(channels, channels, bias=qkv_bias)
            self.to_kv = nn.Linear(ctx_channels, channels * 2, bias=qkv_bias)

        if qk_rms_norm:
            self.q_rms_norm = _SparseMultiHeadRMSNorm(self.head_dim, num_heads)
            self.k_rms_norm = _SparseMultiHeadRMSNorm(self.head_dim, num_heads)

        self.to_out = nn.Linear(channels, channels)

        if use_rope:
            self.rope = SparseRotaryPositionEmbedder(self.head_dim, rope_freq=rope_freq)

    def forward(self, x: SparseTensor, context: Optional[torch.Tensor] = None) -> SparseTensor:
        t = x.feats.shape[0]
        if self.attn_type == "self":
            qkv = self.to_qkv(x.feats).reshape(t, 3, self.num_heads, self.head_dim)
            q, k, v = qkv.unbind(dim=1)
            if self.qk_rms_norm:
                q = self.q_rms_norm(q)
                k = self.k_rms_norm(k)
            if self.use_rope:
                q_sp, k_sp = self.rope(x.replace(q), x.replace(k))
                q, k = q_sp.feats, k_sp.feats
            out = sparse_scaled_dot_product_attention(x.replace(q), x.replace(k), x.replace(v))
        else:
            assert context is not None, "cross-attention requires a context tensor"
            q = self.to_q(x.feats).reshape(t, self.num_heads, self.head_dim)
            n, l, _ = context.shape
            k, v = self.to_kv(context).reshape(n, l, 2, self.num_heads, self.head_dim).unbind(dim=2)
            if self.qk_rms_norm:
                q = self.q_rms_norm(q)
                k = self.k_rms_norm(k)
            out = _sparse_dense_cross_attention(x.replace(q), k, v)
        out_feats = out.feats.reshape(out.feats.shape[0], -1)
        return out.replace(self.to_out(out_feats))


class _SparseGELUTanh(nn.GELU):
    def __init__(self):
        super().__init__(approximate="tanh")

    def forward(self, x: SparseTensor) -> SparseTensor:
        return x.replace(super().forward(x.feats))


class _SparseFeedForwardNet(nn.Module):
    def __init__(self, channels: int, mlp_ratio: float = 4.0):
        super().__init__()
        self.mlp = nn.Sequential(
            SparseLinear(channels, int(channels * mlp_ratio)),
            _SparseGELUTanh(),
            SparseLinear(int(channels * mlp_ratio), channels),
        )

    def forward(self, x: SparseTensor) -> SparseTensor:
        return self.mlp(x)


class _SLatDiTBlock(nn.Module):
    """Derived from ``ModulatedSparseTransformerCrossBlock``: self-attn (sparse
    tokens, rope) + cross-attn (sparse queries, dense cond kv) + FFN, each
    adaLN-modulated from a shared or per-block projection of ``t_emb``."""

    def __init__(
        self,
        channels: int,
        ctx_channels: int,
        num_heads: int,
        mlp_ratio: float = 4.0,
        rope_freq: Tuple[float, float] = (1.0, 10000.0),
        qk_rms_norm: bool = False,
        qk_rms_norm_cross: bool = False,
        qkv_bias: bool = True,
        share_mod: bool = False,
    ):
        super().__init__()
        self.share_mod = share_mod
        self.norm1 = nn.LayerNorm(channels, elementwise_affine=False, eps=1e-6)
        self.norm2 = nn.LayerNorm(channels, elementwise_affine=True, eps=1e-6)
        self.norm3 = nn.LayerNorm(channels, elementwise_affine=False, eps=1e-6)
        self.self_attn = _SparseMultiHeadAttention(
            channels, num_heads, attn_type="self", qkv_bias=qkv_bias,
            use_rope=True, rope_freq=rope_freq, qk_rms_norm=qk_rms_norm,
        )
        self.cross_attn = _SparseMultiHeadAttention(
            channels, num_heads, ctx_channels=ctx_channels, attn_type="cross",
            qkv_bias=qkv_bias, qk_rms_norm=qk_rms_norm_cross,
        )
        self.mlp = _SparseFeedForwardNet(channels, mlp_ratio=mlp_ratio)
        if not share_mod:
            self.adaLN_modulation = nn.Sequential(
                nn.SiLU(), nn.Linear(channels, 6 * channels, bias=True)
            )
        else:
            self.modulation = nn.Parameter(torch.randn(6 * channels) / channels**0.5)

    def forward(self, x: SparseTensor, mod: torch.Tensor, context: torch.Tensor) -> SparseTensor:
        if self.share_mod:
            shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
                (self.modulation + mod).type(mod.dtype).chunk(6, dim=1)
            )
        else:
            shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
                self.adaLN_modulation(mod).chunk(6, dim=1)
            )

        h = x.replace(self.norm1(x.feats))
        h = h * (1 + scale_msa) + shift_msa
        h = self.self_attn(h)
        h = h * gate_msa
        x = x + h

        h = x.replace(self.norm2(x.feats))
        h = self.cross_attn(h, context)
        x = x + h

        h = x.replace(self.norm3(x.feats))
        h = h * (1 + scale_mlp) + shift_mlp
        h = self.mlp(h)
        h = h * gate_mlp
        x = x + h
        return x


class SLatFlowModel(nn.Module):
    """Derived from upstream's ``SLatFlowModel``. ``in_channels``/``out_channels``
    differ between the shape flow (32/32) and the texture flow (64/32, the
    extra 32 channels arriving via ``concat_cond`` — the shape latent)."""

    def __init__(
        self,
        resolution: int,
        in_channels: int,
        model_channels: int,
        cond_channels: int,
        out_channels: int,
        num_blocks: int,
        num_heads: int,
        mlp_ratio: float = 4.0,
        pe_mode: str = "rope",
        rope_freq: Tuple[float, float] = (1.0, 10000.0),
        share_mod: bool = False,
        qk_rms_norm: bool = False,
        qk_rms_norm_cross: bool = False,
        qkv_bias: bool = True,
    ):
        super().__init__()
        if pe_mode != "rope":
            raise NotImplementedError(
                f"pe_mode={pe_mode!r} not ported: every production SLat flow config uses 'rope'"
            )
        self.resolution = resolution
        self.in_channels = in_channels
        self.model_channels = model_channels
        self.cond_channels = cond_channels
        self.out_channels = out_channels
        self.num_blocks = num_blocks
        self.num_heads = num_heads
        self.mlp_ratio = mlp_ratio
        self.pe_mode = pe_mode
        self.share_mod = share_mod
        self.qk_rms_norm = qk_rms_norm
        self.qk_rms_norm_cross = qk_rms_norm_cross

        self.t_embedder = _TimestepEmbedder(model_channels)
        if share_mod:
            self.adaLN_modulation = nn.Sequential(
                nn.SiLU(), nn.Linear(model_channels, 6 * model_channels, bias=True)
            )

        self.input_layer = SparseLinear(in_channels, model_channels)
        self.blocks = nn.ModuleList([
            _SLatDiTBlock(
                model_channels,
                cond_channels,
                num_heads,
                mlp_ratio=mlp_ratio,
                rope_freq=rope_freq,
                qk_rms_norm=qk_rms_norm,
                qk_rms_norm_cross=qk_rms_norm_cross,
                qkv_bias=qkv_bias,
                share_mod=share_mod,
            )
            for _ in range(num_blocks)
        ])
        self.out_layer = SparseLinear(model_channels, out_channels)

    def forward(
        self,
        x: SparseTensor,
        t: torch.Tensor,
        cond: torch.Tensor,
        concat_cond: Optional[SparseTensor] = None,
    ) -> SparseTensor:
        if concat_cond is not None:
            x = sparse_cat([x, concat_cond], dim=-1)

        h = self.input_layer(x)
        t_emb = self.t_embedder(t)
        if self.share_mod:
            t_emb = self.adaLN_modulation(t_emb)

        for block in self.blocks:
            h = block(h, t_emb, cond)

        h = h.replace(F.layer_norm(h.feats, h.feats.shape[-1:]))
        h = self.out_layer(h)
        return h
