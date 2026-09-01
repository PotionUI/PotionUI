# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sparse_structure_flow.py
# (TimestepEmbedder, SparseStructureFlowModel) and trellis2/modules/transformer/
# modulated.py (ModulatedTransformerCrossBlock) + blocks.py (FeedForwardNet) +
# attention/modules.py (MultiHeadRMSNorm, MultiHeadAttention) + attention/rope.py
# (RotaryPositionEmbedder). Adapted to the native ``operations`` seam (torch.nn.Linear
# -> operations.Linear) and the native attention dispatcher
# (src/platform/runtime/native/attention.py) in place of the vendored full_attn sdpa
# branch. Submodule attribute names and nesting are kept IDENTICAL to upstream (incl.
# the block's ``mlp.mlp.*`` double-nesting from FeedForwardNet) so a checkpoint's
# ``model.structure_model.*`` state dict loads with no key remapping.
"""SS flow DiT — ``SSFlowDiT`` (``NativeArchModule``).

Dense (non-sparse) transformer that predicts flow-matching velocity over a dense
``[B, in_channels, R, R, R]`` voxel grid (``R = resolution``). Every token attends
to every other token (self-attn, adaLN-modulated, share_mod'd, RoPE'd across the
3 spatial axes) plus a fixed image-conditioning token set (cross-attn).

Forward-call contract
----------------------
``forward(x, t, cond)``

  * ``x``    — ``[B, in_channels, R, R, R]`` noised latent.
  * ``t``    — ``[B]`` float32 timestep, used AS-IS. The upstream sampler
               (``trellis2/pipelines/samplers/flow_euler.py``) passes
               ``1000 * t`` for ``t`` in ``[0, 1]`` — the x1000 scale happens at
               the CALLER, not inside this model (unlike Anima's raw-sigma
               contract, this one is a deliberate upstream convention, not a
               ``ModelSamplingDiscreteFlow`` multiplier quirk).
  * ``cond`` — ``[B, S, cond_channels]`` image-conditioning tokens
               (cross-attention context).

Returns velocity ``[B, out_channels, R, R, R]``.
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...attention import attention as _dispatch_attention
from ...base import NativeArchModule
from .config import SSFlowConfig

Tensor = torch.Tensor


def _manual_cast(t: Tensor, dtype: torch.dtype) -> Tensor:
    """Cast unless autocast is managing dtype already (mirrors upstream's helper)."""
    if not torch.is_autocast_enabled():
        return t.type(dtype)
    return t


def _norm32(norm: nn.Module, x: Tensor) -> Tensor:
    """Run ``norm`` in float32 regardless of ``x``'s dtype (LayerNorm32)."""
    x_dtype = x.dtype
    o = norm(_manual_cast(x, torch.float32))
    return _manual_cast(o, x_dtype)


def _build_rope_phases(config: SSFlowConfig) -> Tensor:
    """Precompute the [R^3, head_dim//2] complex RoPE phase table for every
    voxel coordinate in an ``R x R x R`` grid, 3 spatial axes packed into the
    head dim (padded with a zero-phase column when it doesn't divide evenly —
    an upstream quirk this reproduces bit-for-bit)."""
    dim = 3
    head_dim = config.head_dim
    freq_dim = head_dim // 2 // dim
    freqs = torch.arange(freq_dim, dtype=torch.float32) / freq_dim
    freqs = config.rope_freq[0] / (config.rope_freq[1] ** freqs)

    axes = [torch.arange(config.resolution) for _ in range(dim)]
    coords = torch.meshgrid(*axes, indexing="ij")
    coords = torch.stack(coords, dim=-1).reshape(-1, dim)

    indices = coords.reshape(-1).float()
    phases = torch.outer(indices, freqs)
    phases = torch.polar(torch.ones_like(phases), phases)
    phases = phases.reshape(*coords.shape[:-1], -1)

    if phases.shape[-1] < head_dim // 2:
        padn = head_dim // 2 - phases.shape[-1]
        phases = torch.cat(
            [phases, torch.polar(torch.ones(*phases.shape[:-1], padn), torch.zeros(*phases.shape[:-1], padn))],
            dim=-1,
        )
    return phases


def _apply_rotary_embedding(x: Tensor, phases: Tensor) -> Tensor:
    x_complex = torch.view_as_complex(x.float().reshape(*x.shape[:-1], -1, 2))
    x_rotated = x_complex * phases.unsqueeze(-2)
    return torch.view_as_real(x_rotated).reshape(*x_rotated.shape[:-1], -1).to(x.dtype)


class _TimestepEmbedder(nn.Module):
    def __init__(self, hidden_size: int, operations, frequency_embedding_size: int = 256,
                 device=None, dtype=None) -> None:
        super().__init__()
        self.mlp = nn.Sequential(
            operations.Linear(frequency_embedding_size, hidden_size, bias=True, device=device, dtype=dtype),
            nn.SiLU(),
            operations.Linear(hidden_size, hidden_size, bias=True, device=device, dtype=dtype),
        )
        self.frequency_embedding_size = frequency_embedding_size

    @staticmethod
    def _timestep_embedding(t: Tensor, dim: int, max_period: int = 10000) -> Tensor:
        half = dim // 2
        freqs = torch.exp(
            -math.log(max_period) * torch.arange(start=0, end=half, dtype=torch.float32, device=t.device) / half
        )
        args = t[:, None].float() * freqs[None]
        embedding = torch.cat([torch.cos(args), torch.sin(args)], dim=-1)
        if dim % 2:
            embedding = torch.cat([embedding, torch.zeros_like(embedding[:, :1])], dim=-1)
        return embedding

    def forward(self, t: Tensor) -> Tensor:
        return self.mlp(self._timestep_embedding(t, self.frequency_embedding_size))


class _MultiHeadRMSNorm(nn.Module):
    def __init__(self, dim: int, heads: int, device=None, dtype=None) -> None:
        super().__init__()
        self.scale = dim**0.5
        self.gamma = nn.Parameter(torch.ones(heads, dim, device=device, dtype=dtype))

    def forward(self, x: Tensor) -> Tensor:
        return (F.normalize(x.float(), dim=-1) * self.gamma * self.scale).to(x.dtype)


class _MultiHeadAttention(nn.Module):
    """Self- or cross-attention. ``attn_type`` picks ``to_qkv`` vs. ``to_q``/
    ``to_kv`` — both submodule shapes upstream's ``MultiHeadAttention`` exposes,
    kept as separate branches so the key space matches whichever one loads."""

    def __init__(self, channels: int, num_heads: int, operations, ctx_channels: int | None = None,
                 attn_type: str = "self", use_rope: bool = False, qk_rms_norm: bool = False,
                 qkv_bias: bool = True, device=None, dtype=None) -> None:
        super().__init__()
        assert channels % num_heads == 0
        assert attn_type in ("self", "cross")
        self.channels = channels
        self.head_dim = channels // num_heads
        self.ctx_channels = ctx_channels if ctx_channels is not None else channels
        self.num_heads = num_heads
        self.attn_type = attn_type
        self.use_rope = use_rope
        self.qk_rms_norm = qk_rms_norm

        if attn_type == "self":
            self.to_qkv = operations.Linear(channels, channels * 3, bias=qkv_bias, device=device, dtype=dtype)
        else:
            self.to_q = operations.Linear(channels, channels, bias=qkv_bias, device=device, dtype=dtype)
            self.to_kv = operations.Linear(self.ctx_channels, channels * 2, bias=qkv_bias, device=device, dtype=dtype)

        if self.qk_rms_norm:
            self.q_rms_norm = _MultiHeadRMSNorm(self.head_dim, num_heads, device=device, dtype=dtype)
            self.k_rms_norm = _MultiHeadRMSNorm(self.head_dim, num_heads, device=device, dtype=dtype)

        self.to_out = operations.Linear(channels, channels, device=device, dtype=dtype)

    def forward(self, x: Tensor, context: Tensor | None = None, phases: Tensor | None = None) -> Tensor:
        B, L, _ = x.shape
        if self.attn_type == "self":
            qkv = self.to_qkv(x).reshape(B, L, 3, self.num_heads, -1)
            q, k, v = qkv.unbind(dim=2)
            if self.qk_rms_norm:
                q = self.q_rms_norm(q)
                k = self.k_rms_norm(k)
            if self.use_rope:
                assert phases is not None, "phases must be provided for RoPE"
                q = _apply_rotary_embedding(q, phases)
                k = _apply_rotary_embedding(k, phases)
        else:
            Lkv = context.shape[1]
            q = self.to_q(x).reshape(B, L, self.num_heads, -1)
            kv = self.to_kv(context).reshape(B, Lkv, 2, self.num_heads, -1)
            k, v = kv.unbind(dim=2)
            if self.qk_rms_norm:
                q = self.q_rms_norm(q)
                k = self.k_rms_norm(k)

        # (B, L, H, D) -> (B, H, L, D) for the dispatcher's contract, back after.
        h = _dispatch_attention(q.transpose(1, 2), k.transpose(1, 2), v.transpose(1, 2)).transpose(1, 2)
        h = h.reshape(B, L, -1)
        return self.to_out(h)


class _FeedForwardNet(nn.Module):
    """Wraps its Sequential in a ``mlp`` attribute (upstream's own nesting) so a
    block's ``self.mlp = _FeedForwardNet(...)`` yields ``mlp.mlp.0``/``mlp.mlp.2``
    keys, matching ``FeedForwardNet`` verbatim."""

    def __init__(self, channels: int, mlp_ratio: float, operations, device=None, dtype=None) -> None:
        super().__init__()
        hidden = int(channels * mlp_ratio)
        self.mlp = nn.Sequential(
            operations.Linear(channels, hidden, device=device, dtype=dtype),
            nn.GELU(approximate="tanh"),
            operations.Linear(hidden, channels, device=device, dtype=dtype),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.mlp(x)


class _SSFlowBlock(nn.Module):
    """``ModulatedTransformerCrossBlock``: self-attn (RoPE) + cross-attn + MLP,
    each adaLN-modulated from a shared per-model (``share_mod``) or per-block
    modulation MLP."""

    def __init__(self, config: SSFlowConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        channels = config.model_channels
        self.share_mod = config.share_mod

        self.norm1 = operations.LayerNorm(channels, elementwise_affine=False, eps=1e-6, device=device, dtype=dtype)
        self.norm2 = operations.LayerNorm(channels, elementwise_affine=True, eps=1e-6, device=device, dtype=dtype)
        self.norm3 = operations.LayerNorm(channels, elementwise_affine=False, eps=1e-6, device=device, dtype=dtype)

        self.self_attn = _MultiHeadAttention(
            channels, config.num_heads, operations, attn_type="self",
            use_rope=(config.pe_mode == "rope"), qk_rms_norm=config.qk_rms_norm,
            device=device, dtype=dtype,
        )
        self.cross_attn = _MultiHeadAttention(
            channels, config.num_heads, operations, ctx_channels=config.cond_channels,
            attn_type="cross", qk_rms_norm=config.qk_rms_norm_cross,
            device=device, dtype=dtype,
        )
        self.mlp = _FeedForwardNet(channels, config.mlp_ratio, operations, device=device, dtype=dtype)

        if not self.share_mod:
            self.adaLN_modulation = nn.Sequential(
                nn.SiLU(),
                operations.Linear(channels, 6 * channels, bias=True, device=device, dtype=dtype),
            )
        else:
            self.modulation = nn.Parameter(torch.empty(6 * channels, device=device, dtype=dtype))

    def forward(self, x: Tensor, mod: Tensor, context: Tensor, phases: Tensor | None = None) -> Tensor:
        if self.share_mod:
            shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
                (self.modulation + mod).type(mod.dtype).chunk(6, dim=1)
            )
        else:
            shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = self.adaLN_modulation(mod).chunk(6, dim=1)

        h = _norm32(self.norm1, x)
        h = h * (1 + scale_msa.unsqueeze(1)) + shift_msa.unsqueeze(1)
        h = self.self_attn(h, phases=phases)
        h = h * gate_msa.unsqueeze(1)
        x = x + h

        h = _norm32(self.norm2, x)
        h = self.cross_attn(h, context)
        x = x + h

        h = _norm32(self.norm3, x)
        h = h * (1 + scale_mlp.unsqueeze(1)) + shift_mlp.unsqueeze(1)
        h = self.mlp(h)
        h = h * gate_mlp.unsqueeze(1)
        x = x + h
        return x


class SSFlowDiT(NativeArchModule):
    """``SparseStructureFlowModel``: dense DiT over an ``R^3`` voxel grid."""

    def __init__(self, config: SSFlowConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.config = config
        mc = config.model_channels

        self.t_embedder = _TimestepEmbedder(mc, operations, device=device, dtype=dtype)
        if config.share_mod:
            self.adaLN_modulation = nn.Sequential(
                nn.SiLU(),
                operations.Linear(mc, 6 * mc, bias=True, device=device, dtype=dtype),
            )

        if config.pe_mode == "rope":
            self.register_buffer("rope_phases", _build_rope_phases(config))
        else:
            self.rope_phases = None

        self.input_layer = operations.Linear(config.in_channels, mc, device=device, dtype=dtype)
        self.blocks = nn.ModuleList([
            _SSFlowBlock(config, operations, device=device, dtype=dtype) for _ in range(config.num_blocks)
        ])
        self.out_layer = operations.Linear(mc, config.out_channels, device=device, dtype=dtype)

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "SSFlowDiT":
        return cls(SSFlowConfig(**config), operations=operations)

    def post_load(self) -> None:
        """Recompute the RoPE phase table on the module's real device — a
        complex buffer, so it is expected-missing from any real (safetensors)
        checkpoint and must be regenerated rather than assign-loaded."""
        if self.config.pe_mode == "rope":
            device = self.input_layer.weight.device
            self.rope_phases = _build_rope_phases(self.config).to(device=device)

    # -- forward --------------------------------------------------------------

    def forward(self, x: Tensor, t: Tensor, cond: Tensor) -> Tensor:
        config = self.config
        expected = [x.shape[0], config.in_channels] + [config.resolution] * 3
        assert list(x.shape) == expected, f"Input shape mismatch, got {x.shape}, expected {expected}"

        h = x.view(*x.shape[:2], -1).permute(0, 2, 1).contiguous()
        h = self.input_layer(h)

        t_emb = self.t_embedder(t)
        if config.share_mod:
            t_emb = self.adaLN_modulation(t_emb)
        t_emb = _manual_cast(t_emb, h.dtype)
        cond = _manual_cast(cond, h.dtype)

        for block in self.blocks:
            h = block(h, t_emb, cond, self.rope_phases)

        h = _manual_cast(h, x.dtype)
        h = F.layer_norm(h, h.shape[-1:])
        h = self.out_layer(h)

        h = h.permute(0, 2, 1).view(h.shape[0], h.shape[2], *[config.resolution] * 3).contiguous()
        return h
